"""The graph-facing HookNode and its internal invocation Port."""

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import (
    HookConfigSource,
    HookContractError,
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookPlanLoader,
    HookRequest,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookPriority, HookSlotId, hook_definition_id
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
from mote_kernel.invocation import Invocation

ConfigT = TypeVar("ConfigT")
PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


@dataclass(frozen=True, slots=True)
class _HookPort(Generic[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]):
    """Adapt one priority plan to one transport-independent invocation."""

    admission: HookPayloadAdmission[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]
    invocation: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT, StateT],
        HookStageResult[ValueT, CommandT],
    ]

    async def execute(
        self,
        plan: HookPriorityPlan[PriorityConfigT],
        request: HookRequest[ValueT, StateT],
        /,
    ) -> HookStageResult[ValueT, CommandT]:
        admitted_request = self.admission.admit_request(request)
        invocation_request = self.admission.admit_invocation_request(
            HookInvocationRequest(plan.config, admitted_request)
        )
        result = await self.invocation.invoke(invocation_request)
        return self.admission.admit_stage_result(result)


@dataclass(frozen=True, slots=True)
class _HookProgress(
    HookGraphValue,
    Generic[PriorityConfigT, ValueT, StateT, CommandT],
):
    request: HookRequest[ValueT, StateT]
    plan: HookPlan[PriorityConfigT]
    commands: tuple[CommandT, ...]


@dataclass(frozen=True, slots=True)
class _PlanNode(
    Generic[ConfigT, PriorityConfigT, ValueT, StateT, CommandT],
):
    config_source: HookConfigSource[ConfigT]
    plan_loader: HookPlanLoader[ConfigT, PriorityConfigT]
    admission: HookPayloadAdmission[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        request = self.admission.admit_request(cast(HookRequest[ValueT, StateT], values["request"]))
        snapshot = self.config_source.snapshot()
        snapshot = self.admission.admit_snapshot(snapshot)
        plan = self.plan_loader.load(snapshot)
        plan = self.admission.admit_plan(plan)
        return Graph.values(
            progress=_HookProgress(
                request,
                plan,
                (),
            )
        )


@dataclass(frozen=True, slots=True)
class _PlannedPriorityNode(Generic[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]):
    priority: Literal[HookPriority.P1, HookPriority.P2, HookPriority.P3]
    port: _HookPort[ConfigT, PriorityConfigT, ValueT, StateT, CommandT]

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        progress = cast(
            _HookProgress[PriorityConfigT, ValueT, StateT, CommandT],
            values["progress"],
        )
        if self.priority is HookPriority.P1:
            priority_plan = progress.plan.p1
        elif self.priority is HookPriority.P2:
            priority_plan = progress.plan.p2
        else:
            priority_plan = progress.plan.p3
        result = await self.port.execute(priority_plan, progress.request)
        ordered_commands = progress.commands + result.commands
        if self.priority is HookPriority.P3:
            return Graph.values(result=self.port.admission.admit_result(HookResult(result.value, ordered_commands)))
        return Graph.values(
            progress=_HookProgress(
                HookRequest(result.value, progress.request.state),
                progress.plan,
                ordered_commands,
            )
        )


class HookNode(
    Graph[HookGraphValue],
    Generic[ConfigT, PriorityConfigT, ValueT, StateT, CommandT],
):
    """A typed plan -> P1 -> P2 -> P3 Graph using one dynamic plan."""

    __slots__ = ("_slot",)

    def __init__(
        self,
        slot: HookSlotId,
        config_source: HookConfigSource[ConfigT] | None,
        plan_loader: HookPlanLoader[ConfigT, PriorityConfigT] | None,
        invocation: Invocation[
            HookInvocationRequest[PriorityConfigT, ValueT, StateT],
            HookStageResult[ValueT, CommandT],
        ]
        | None,
        payload_admission: HookPayloadAdmission[ConfigT, PriorityConfigT, ValueT, StateT, CommandT] | None,
    ) -> None:
        if type(slot) is not HookSlotId:
            raise HookContractError("hook node requires a HookSlotId")
        if not isinstance(config_source, HookConfigSource) or not callable(config_source.snapshot):
            raise HookContractError("hook node requires a config source")
        if not isinstance(plan_loader, HookPlanLoader) or not callable(plan_loader.load):
            raise HookContractError("hook node requires a plan loader")
        if not isinstance(invocation, Invocation) or not callable(invocation.invoke):
            raise HookContractError("hook node requires an invocation capability")
        if type(payload_admission) is not HookPayloadAdmission:
            raise HookContractError("hook node requires a payload admission contract")

        super().__init__(
            hook_definition_id(slot),
            version=int(slot.definition_version),
        )
        self._slot = slot

        request_type = cast(type[HookGraphValue], HookRequest)
        progress_type = cast(type[HookGraphValue], _HookProgress)
        result_type = cast(type[HookGraphValue], HookResult)
        request_input = Graph.graph_input("request", request_type)
        port = _HookPort(payload_admission, invocation)
        plan = _PlanNode[ConfigT, PriorityConfigT, ValueT, StateT, CommandT](
            config_source,
            plan_loader,
            payload_admission,
        )
        p1 = _PlannedPriorityNode[ConfigT, PriorityConfigT, ValueT, StateT, CommandT](
            HookPriority.P1,
            port,
        )
        p2 = _PlannedPriorityNode[ConfigT, PriorityConfigT, ValueT, StateT, CommandT](
            HookPriority.P2,
            port,
        )
        p3 = _PlannedPriorityNode[ConfigT, PriorityConfigT, ValueT, StateT, CommandT](
            HookPriority.P3,
            port,
        )

        self.add_node(
            "plan",
            plan,
            inputs={"request": request_input},
            outputs={"progress": progress_type},
        )
        self.add_node(
            "p1",
            p1,
            inputs={"progress": Graph.node_output("plan", "progress")},
            outputs={"progress": progress_type},
        )
        self.add_node(
            "p2",
            p2,
            inputs={"progress": Graph.node_output("p1", "progress")},
            outputs={"progress": progress_type},
        )
        self.add_node(
            "p3",
            p3,
            inputs={"progress": Graph.node_output("p2", "progress")},
            outputs={"result": result_type},
        )
        self.add_edge("plan", "p1")
        self.add_edge("p1", "p2")
        self.add_edge("p2", "p3")
        self.add_edge("p3", Graph.END)
        self.set_outputs({"result": Graph.node_output("p3", "result")})

    @property
    def slot(self) -> HookSlotId:
        """Return the immutable assembly slot used to define this HookNode."""

        return self._slot


__all__ = ["HookNode"]

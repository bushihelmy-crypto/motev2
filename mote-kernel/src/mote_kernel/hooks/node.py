"""The graph-facing HookNode and its internal invocation Port."""

from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import (
    HookConfigSource,
    HookContractError,
    HookGraphValue,
    HookPlanLoader,
    HookRequest,
    HookResult,
)
from mote_kernel.hooks.identity import HookPriority, HookSlotId
from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan, HookPriorityPlan

ConfigT = TypeVar("ConfigT")
PriorityConfigT = TypeVar("PriorityConfigT")
_InvocationConfigT_contra = TypeVar("_InvocationConfigT_contra", contravariant=True)
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


class _Invocation(Protocol[_InvocationConfigT_contra, ValueT, StateT, CommandT]):
    """Kernel-side view implemented by the binding to the shared invocation engine."""

    async def invoke(
        self,
        config: _InvocationConfigT_contra,
        request: HookRequest[ValueT, StateT],
        /,
    ) -> HookResult[ValueT, CommandT]: ...


@dataclass(frozen=True, slots=True)
class _HookPort(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """Adapt one priority plan to one transport-independent invocation."""

    invocation: _Invocation[PriorityConfigT, ValueT, StateT, CommandT]

    async def execute(
        self,
        plan: HookPriorityPlan[PriorityConfigT],
        request: HookRequest[ValueT, StateT],
        /,
    ) -> HookResult[ValueT, CommandT]:
        result = await self.invocation.invoke(plan.config, request)
        if type(result) is not HookResult:
            raise HookContractError("hook invocation must return a HookResult")
        return result


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

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        request = cast(HookRequest[ValueT, StateT], values["request"])
        snapshot = self.config_source.snapshot()
        if type(snapshot) is not HookConfigSnapshot:
            raise HookContractError("hook config source must return a HookConfigSnapshot")
        plan = self.plan_loader.load(snapshot)
        if type(plan) is not HookPlan:
            raise HookContractError("hook plan loader must return a HookPlan")
        return Graph.values(
            progress=_HookProgress(
                request,
                plan,
                (),
            )
        )


@dataclass(frozen=True, slots=True)
class _PlannedPriorityNode(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    priority: Literal[HookPriority.P1, HookPriority.P2, HookPriority.P3]
    port: _HookPort[PriorityConfigT, ValueT, StateT, CommandT]

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
        combined_commands = (*progress.commands, *result.commands)
        if self.priority is HookPriority.P3:
            return Graph.values(result=HookResult(result.value, combined_commands))
        return Graph.values(
            progress=_HookProgress(
                HookRequest(result.value, progress.request.state),
                progress.plan,
                combined_commands,
            )
        )


class HookNode(
    Graph[HookGraphValue],
    Generic[ConfigT, PriorityConfigT, ValueT, StateT, CommandT],
):
    """A typed plan -> P1 -> P2 -> P3 Graph using one dynamic plan."""

    __slots__ = ("slot",)

    def __init__(
        self,
        slot: HookSlotId,
        config_source: HookConfigSource[ConfigT] | None,
        plan_loader: HookPlanLoader[ConfigT, PriorityConfigT] | None,
        invocation: _Invocation[PriorityConfigT, ValueT, StateT, CommandT] | None,
    ) -> None:
        if type(slot) is not HookSlotId:
            raise HookContractError("hook node requires a HookSlotId")
        if config_source is None:
            raise HookContractError("hook node requires a config source")
        if plan_loader is None:
            raise HookContractError("hook node requires a plan loader")
        if invocation is None:
            raise HookContractError("hook node requires an invocation capability")

        super().__init__(
            f"{slot.definition_id}.hook.{slot.node_id}.{slot.stage.name.lower()}",
            version=int(slot.definition_version),
        )
        self.slot = slot

        request_type = cast(type[HookGraphValue], HookRequest)
        progress_type = cast(type[HookGraphValue], _HookProgress)
        result_type = cast(type[HookGraphValue], HookResult)
        request_input = Graph.graph_input("request", request_type)
        port = _HookPort(invocation)
        plan = _PlanNode[ConfigT, PriorityConfigT, ValueT, StateT, CommandT](
            config_source,
            plan_loader,
        )
        p1 = _PlannedPriorityNode[PriorityConfigT, ValueT, StateT, CommandT](
            HookPriority.P1,
            port,
        )
        p2 = _PlannedPriorityNode[PriorityConfigT, ValueT, StateT, CommandT](
            HookPriority.P2,
            port,
        )
        p3 = _PlannedPriorityNode[PriorityConfigT, ValueT, StateT, CommandT](
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


__all__ = ["HookNode"]

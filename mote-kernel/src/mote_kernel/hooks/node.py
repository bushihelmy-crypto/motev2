"""The graph-facing HookNode and its internal invocation Port."""

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import (
    HookContractError,
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookRequest,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId, hook_definition_id
from mote_kernel.hooks.plan import HookPlan
from mote_kernel.hooks.port import HookPort
from mote_kernel.invocation import Invocation

PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


@dataclass(frozen=True, slots=True)
class _HookProgress(
    HookGraphValue,
    Generic[ValueT, StateT, CommandT],
):
    request: HookRequest[ValueT, StateT]
    commands: tuple[CommandT, ...]


@dataclass(frozen=True, slots=True)
class _P1Node(
    Generic[PriorityConfigT, ValueT, StateT, CommandT],
):
    plan: HookPlan[PriorityConfigT]
    port: HookPort[PriorityConfigT, ValueT, StateT, CommandT]

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        admission = self.port.admission
        request = admission.admit_request(cast(HookRequest[ValueT, StateT], values["request"]))
        result = await self.port.execute(self.plan.p1, request)
        return Graph.values(
            progress=_HookProgress(
                HookRequest(result.value, request.state, request.node_id),
                result.commands,
            )
        )


@dataclass(frozen=True, slots=True)
class _P2Node(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    plan: HookPlan[PriorityConfigT]
    port: HookPort[PriorityConfigT, ValueT, StateT, CommandT]

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue] | Graph.Outcome[HookGraphValue]:
        progress = cast(
            _HookProgress[ValueT, StateT, CommandT],
            values["progress"],
        )
        result = await self.port.execute(self.plan.p2, progress.request)
        ordered_commands = progress.commands + result.commands
        hook_result = self.port.admission.admit_result(
            HookResult(result.value, ordered_commands, progress.request.node_id)
        )
        output = Graph.values(result=hook_result)
        # The shared Hook only reports which business node supplied the
        # request.  It does not know the containing graph's topology.  A
        # parent graph declares the meaning of that opaque node token on
        # its own conditional edges; the token is carried as the nested
        # graph's terminal route.
        if hook_result.node_id is None:
            return output
        return Graph.success(output, route=str(hook_result.node_id))


class HookNode(
    Graph[HookGraphValue],
    Generic[PriorityConfigT, ValueT, StateT, CommandT],
):
    """A typed P1 -> P2 Graph using one assembly-time plan."""

    __slots__ = ("_payload_admission", "_slot")

    def __init__(
        self,
        slot: HookSlotId,
        plan: HookPlan[PriorityConfigT] | None,
        invocation: Invocation[
            HookInvocationRequest[PriorityConfigT, ValueT, StateT],
            HookStageResult[ValueT, CommandT],
        ]
        | None,
        payload_admission: HookPayloadAdmission[PriorityConfigT, ValueT, StateT, CommandT] | None,
    ) -> None:
        if type(slot) is not HookSlotId:
            raise HookContractError("hook node requires a HookSlotId")
        if not isinstance(invocation, Invocation) or not callable(invocation.invoke):
            raise HookContractError("hook node requires an invocation capability")
        if type(payload_admission) is not HookPayloadAdmission:
            raise HookContractError("hook node requires a payload admission contract")
        if type(plan) is not HookPlan:
            raise HookContractError("hook node requires a HookPlan")
        plan = payload_admission.admit_plan(plan)

        super().__init__(
            hook_definition_id(slot),
            version=int(slot.definition_version),
        )
        self._payload_admission = payload_admission
        self._slot = slot

        request_type = cast(type[HookGraphValue], HookRequest)
        progress_type = cast(type[HookGraphValue], _HookProgress)
        result_type = cast(type[HookGraphValue], HookResult)
        request_input = Graph.graph_input("request", request_type)
        port = HookPort(payload_admission, invocation)
        p1 = _P1Node[PriorityConfigT, ValueT, StateT, CommandT](plan, port)
        p2 = _P2Node[PriorityConfigT, ValueT, StateT, CommandT](plan, port)

        self.add_node(
            "p1",
            p1,
            inputs={"request": request_input},
            outputs={"progress": progress_type},
        )
        self.add_node(
            "p2",
            p2,
            inputs={"progress": Graph.node_output("p1", "progress")},
            outputs={"result": result_type},
        )
        self.add_edge("p1", "p2")
        self.add_edge("p2", Graph.END)
        # Graph owns typed output resolution, including nested boundaries.
        self.set_outputs({"result": self.output_ref("p2", "result")})

    @property
    def slot(self) -> HookSlotId:
        """Return the immutable assembly slot used to define this HookNode."""

        return self._slot

    @property
    def payload_admission(
        self,
    ) -> HookPayloadAdmission[PriorityConfigT, ValueT, StateT, CommandT]:
        """Return the immutable concrete payload contract bound at assembly."""

        return self._payload_admission


__all__ = ["HookNode"]

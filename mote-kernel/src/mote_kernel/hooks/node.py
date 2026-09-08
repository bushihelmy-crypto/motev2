"""The graph-facing HookNode and its internal invocation Port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.config import Config, require_config
from mote_kernel.execution import Graph
from mote_kernel.hooks.config import (
    HookBinding,
    HookPriorityBinding,
    HookPriorityConfig,
)
from mote_kernel.hooks.contract import (
    HookActivationRequest,
    HookContractError,
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookPriority, HookSlotId, hook_definition_id
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
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
    request: HookActivationRequest[ValueT, StateT]
    commands: tuple[CommandT, ...]


@dataclass(frozen=True, slots=True)
class _P1Node(
    Generic[PriorityConfigT, ValueT, StateT, CommandT],
):
    plan: HookPriorityPlan[PriorityConfigT]
    port: HookPort[PriorityConfigT, ValueT, StateT, CommandT]
    binding: HookPriorityBinding[PriorityConfigT, ValueT, StateT, CommandT]
    slot: HookSlotId

    def _runtime(
        self,
        config: Config | None,
        /,
    ) -> tuple[
        HookPriorityPlan[PriorityConfigT],
        HookPort[PriorityConfigT, ValueT, StateT, CommandT],
    ]:
        if config is None:
            return self.plan, self.port
        selected = config.bind(self.binding)
        if type(selected) is not HookPriorityConfig:
            raise HookContractError("Hook config binding returned an invalid projection")
        if selected.slot != self.slot or selected.payload_admission != self.port.admission:
            raise HookContractError("Hook config binding changed the compiled Hook contract")
        return selected.priority_plan, HookPort(self.port.admission, selected.invocation)

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        request_value = cast(HookActivationRequest[ValueT, StateT], values["request"])
        config = values.activation_config
        priority_plan, port = self._runtime(config)
        admission = port.admission
        request = admission.admit_request(request_value)
        cursor = config.config_cursor if config is not None else None
        result = await port.execute(priority_plan, request, cursor)
        return Graph.values(
            progress=_HookProgress(
                HookActivationRequest(
                    result.value,
                    request.state,
                    request.node_id,
                ),
                result.commands,
            )
        )


@dataclass(frozen=True, slots=True)
class _P2Node(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    plan: HookPriorityPlan[PriorityConfigT]
    port: HookPort[PriorityConfigT, ValueT, StateT, CommandT]
    binding: HookPriorityBinding[PriorityConfigT, ValueT, StateT, CommandT]
    slot: HookSlotId

    def _runtime(
        self,
        config: Config | None,
        /,
    ) -> tuple[
        HookPriorityPlan[PriorityConfigT],
        HookPort[PriorityConfigT, ValueT, StateT, CommandT],
    ]:
        if config is None:
            return self.plan, self.port
        selected = config.bind(self.binding)
        if type(selected) is not HookPriorityConfig:
            raise HookContractError("Hook config binding returned an invalid projection")
        if selected.slot != self.slot or selected.payload_admission != self.port.admission:
            raise HookContractError("Hook config binding changed the compiled Hook contract")
        return selected.priority_plan, HookPort(self.port.admission, selected.invocation)

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue] | Graph.Outcome[HookGraphValue]:
        progress = cast(
            _HookProgress[ValueT, StateT, CommandT],
            values["progress"],
        )
        request = progress.request
        config = values.activation_config
        priority_plan, port = self._runtime(config)
        cursor = config.config_cursor if config is not None else None
        result = await port.execute(priority_plan, request, cursor)
        ordered_commands = progress.commands + result.commands
        hook_result = port.admission.admit_result(
            HookResult(
                result.value,
                ordered_commands,
                request.node_id,
            )
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

    @classmethod
    def from_config(
        cls,
        config: Config,
        slot: HookSlotId,
        /,
    ) -> HookNode[PriorityConfigT, ValueT, StateT, CommandT]:
        """Assemble one Hook from the complete config and its declared slot.

        The complete :class:`Config` is intentionally accepted at this one
        assembly boundary.  ``HookBinding`` immediately narrows it to the
        selected slot, and the resulting node stores only that narrow plan,
        invocation, and payload contract.
        """

        config = require_config(config)
        selected = config.bind(HookBinding[PriorityConfigT, ValueT, StateT, CommandT](slot))
        return cls(
            selected.slot,
            selected.plan,
            selected.invocation,
            selected.payload_admission,
        )

    def __init__(
        self,
        slot: HookSlotId,
        plan: HookPlan[PriorityConfigT] | None,
        invocation: Invocation[
            HookInvocationRequest[PriorityConfigT, ValueT],
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

        request_type = cast(type[HookGraphValue], HookActivationRequest)
        progress_type = cast(type[HookGraphValue], _HookProgress)
        result_type = cast(type[HookGraphValue], HookResult)
        request_input = Graph.graph_input("request", request_type)
        port = HookPort(payload_admission, invocation)
        p1_binding = HookPriorityBinding[PriorityConfigT, ValueT, StateT, CommandT](
            slot,
            HookPriority.P1,
        )
        p2_binding = HookPriorityBinding[PriorityConfigT, ValueT, StateT, CommandT](
            slot,
            HookPriority.P2,
        )
        p1 = _P1Node[PriorityConfigT, ValueT, StateT, CommandT](plan.p1, port, p1_binding, slot)
        p2 = _P2Node[PriorityConfigT, ValueT, StateT, CommandT](plan.p2, port, p2_binding, slot)

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
        self.add_edge(Graph.START, "p1")
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

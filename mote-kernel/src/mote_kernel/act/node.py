"""Assembly owner for the public Act tool-use graph."""

from __future__ import annotations

from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.authorize import AuthorizeNode
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
    Allow,
    AuthorizationDecision,
    AuthorizationInterruptView,
    AuthorizeNodeInput,
    Deny,
    ExecuteNodeInput,
    HookStateProjection,
    OpaqueGraphFailureReason,
    ResolveStageValue,
    ResumedAuthorization,
    SettleNodeInput,
)
from mote_kernel.act.execute import ExecuteNode
from mote_kernel.act.identity import ActHookStage, ActSlotId
from mote_kernel.act.port import (
    AuthorizePort,
    ExecutePort,
    ResolvePort,
    SettlementPort,
    ToolExchangeWriter,
    require_act_port_contracts,
)
from mote_kernel.act.resolve import ResolveNode
from mote_kernel.act.settle import SettleNode
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import NodeOutputRef
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import HookGraphValue, HookPayloadAdmission, HookRequest, HookResult
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.state.graph_state import GraphDefinitionId, GraphNodeId

PriorityConfigT = TypeVar("PriorityConfigT")
HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ActHookCommand)


class ActNode(
    Graph[HookGraphValue],
    Generic[PriorityConfigT, HookStateT, HookCommandT],
):
    """The four-stage Act graph with one shared Hook.

    ``ResolveNode``, ``AuthorizeNode``, ``ExecuteNode``, ``SettleNode`` and
    the shared ``HookNode`` are assembled into the canonical Graph topology.
    Every business stage enters the shared Hook.  The Hook returns the
    identity of the business stage it processed; the containing Graph maps
    that identity through static conditional edges.  There is no route
    callable or second routing node.
    """

    __slots__ = (
        "_admission",
        "_authorize_port",
        "_hook",
    )

    def __init__(
        self,
        definition_id: str,
        *,
        version: int = 1,
        resolve_port: ResolvePort,
        authorize_port: AuthorizePort,
        execute_port: ExecutePort,
        settlement_port: SettlementPort,
        exchange_writer: ToolExchangeWriter,
        hook: HookNode[
            PriorityConfigT,
            ActHookEnvelope,
            HookStateT,
            HookCommandT,
        ],
        failure_reason: OpaqueGraphFailureReason,
        admission: ActPayloadAdmission[HookStateT, HookCommandT],
    ) -> None:
        if type(admission) is not ActPayloadAdmission:
            raise ActContractError("ActNode requires an ActPayloadAdmission")
        admission.admit_graph_failure_reason(failure_reason)
        codec_id, codec_version = require_act_port_contracts(
            resolve_port,
            authorize_port,
            execute_port,
            settlement_port,
            exchange_writer,
        )

        if type(hook) is not HookNode:
            raise ActContractError("ActNode requires one shared HookNode")
        hook_admission = hook.payload_admission
        if type(hook_admission) is not HookPayloadAdmission:
            raise ActContractError("ActNode shared Hook must expose a HookPayloadAdmission")
        if hook_admission.value_type is not ActHookEnvelope:
            raise ActContractError("ActNode shared Hook value type must be ActHookEnvelope")
        if hook_admission.state_type is not admission.hook_state_type:
            raise ActContractError("ActNode shared Hook state type does not match Act admission")
        if hook_admission.command_type is not admission.hook_command_type:
            raise ActContractError("ActNode shared Hook command type does not match Act admission")
        if hook_admission.transition_admission is not admission:
            raise ActContractError("ActNode shared Hook must use its ActPayloadAdmission for transitions")
        hook_slot = hook.slot
        if type(hook_slot) is not HookSlotId:
            raise ActContractError("ActNode shared Hook must expose a HookSlotId")
        if (
            hook_slot.definition_id != GraphDefinitionId(definition_id)
            or int(hook_slot.definition_version) != version
            or hook_slot.node_id != GraphNodeId("hook")
            or hook_slot.stage is not HookStage.AFTER_NODE
        ):
            raise ActContractError("ActNode shared HookSlotId does not match its definition")

        for stage in ActHookStage:
            admission.admit_act_slot(ActSlotId(definition_id, version, stage.value))

        # Construct every callable before touching the Graph builder so a
        # failed capability assembly cannot leave a partial definition.
        resolve = ResolveNode[HookStateT, HookCommandT](resolve_port, admission)
        authorize = AuthorizeNode[HookStateT, HookCommandT](authorize_port, failure_reason, admission)
        execute = ExecuteNode[HookStateT, HookCommandT](execute_port, admission)
        settle = SettleNode[HookStateT, HookCommandT](settlement_port, exchange_writer, admission)

        super().__init__(definition_id, version=version)
        self._authorize_port = authorize_port
        self._hook = hook
        self._admission = admission

        request_binding = Graph.bind(
            "request",
            Graph.graph_input("request", ActRequest),
        )

        # Authorize owns the sole codec/correlation capability.  Install it
        # exactly once on this Graph; Graph remains authoritative for resume
        # scope, state, interrupt and codec admission.
        self.set_resume_codec(
            codec_id,
            codec_version,
            authorize_port.encode_graph_input,
            authorize_port.decode_graph_input,
        )

        resolve_output = self.add_node(
            "resolve",
            resolve,
            inputs=(request_binding,),
            input_type=ActRequest,
            materialize=lambda values: values.get(request_binding),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "hook",
            hook,
            inputs={"request": Graph.node_output(resolve_output)},
        )
        # Resolve the nested Hook's declared boundary through the generic
        # Graph API.  This preserves the child descriptor identity for every
        # downstream typed binding and for this graph's public output.
        hook_result_ref = cast(
            NodeOutputRef[HookResult[ActHookEnvelope, HookCommandT]],
            self.output_ref("hook", "result"),
        )
        authorize_hook_result_binding = Graph.bind("hook_result", hook_result_ref)
        stage_hook_result_binding = Graph.bind("hook_result", Graph.node_output(hook_result_ref))
        self.add_node(
            "authorize",
            authorize,
            # The resume boundary may provide an explicit authorization input;
            # bind normal activations to the most recent Hook publication
            # selected by the compiler.  A predecessor-only binding cannot be
            # resumed with an override, so the fixed Hook source is required
            # for this interruptible stage.
            inputs=(authorize_hook_result_binding,),
            input_type=AuthorizeNodeInput,
            materialize=lambda values: AuthorizeNodeInput(values.get(authorize_hook_result_binding)),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "execute",
            execute,
            inputs=(stage_hook_result_binding,),
            input_type=ExecuteNodeInput,
            materialize=lambda values: ExecuteNodeInput(values.get(stage_hook_result_binding)),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "settle",
            settle,
            inputs=(stage_hook_result_binding,),
            input_type=SettleNodeInput,
            materialize=lambda values: SettleNodeInput(values.get(stage_hook_result_binding)),
            output_name="hook_request",
            output_type=HookRequest,
        )
        for business_node in ("resolve", "authorize", "execute", "settle"):
            self.add_edge(business_node, "hook")
        # Hook returns the current business node identity as its terminal
        # route.  Act owns the mapping from that identity to its next stage.
        self.add_edge("hook", "resolve", "authorize")
        self.add_edge("hook", "authorize", "execute")
        self.add_edge("hook", "execute", "settle")
        self.add_edge("hook", "settle", Graph.END)
        self.set_outputs({"result": hook_result_ref})

    @property
    def hook(
        self,
    ) -> HookNode[
        PriorityConfigT,
        ActHookEnvelope,
        HookStateT,
        HookCommandT,
    ]:
        """Return the one shared HookNode installed during assembly."""

        return self._hook

    def resume_authorization(
        self,
        *,
        awaiting: Graph.AwaitingResumeResult[HookGraphValue],
        interrupt_id: str,
        decision: AuthorizationDecision,
    ) -> Graph.ResumeAction[HookGraphValue]:
        """Build the typed Authorize override for a public awaiting result."""

        if type(decision) not in (Allow, Deny):
            raise ActContractError("authorization decision must be Allow or Deny")
        matches = tuple(interrupt for interrupt in awaiting.interrupts if str(interrupt.interrupt_id) == interrupt_id)
        if len(matches) != 1:
            raise ActContractError("authorization interrupt_id must identify exactly one awaiting interrupt")
        interrupt = matches[0]
        view = self._admission.admit_interrupt_view(
            AuthorizationInterruptView(
                tuple(str(segment) for segment in interrupt.scope),
                str(interrupt.node_id),
                str(interrupt.interrupt_id),
                interrupt.request_payload,
            )
        )
        resumed = self._authorize_port.build_resume_input(view, decision)
        resumed = self._admission.admit_authorization_input(resumed)
        if type(resumed.phase) is not ResumedAuthorization:
            raise ActContractError("AuthorizePort resume input must use ResumedAuthorization")
        envelope = ActHookEnvelope(
            ActHookStage.RESOLVE,
            # The resumed input is intentionally sent through the same
            # HookResult override descriptor as a normal Authorize input.
            ResolveStageValue(resumed),
            resumed.phase.resolved.request.hook_state,
        )
        hook_result: HookResult[ActHookEnvelope, HookCommandT] = HookResult(
            envelope,
            (),
            GraphNodeId("resolve"),
        )
        self._admission.admit_hook_result(hook_result)
        return self.resume_interrupted(
            "authorize",
            str(interrupt.interrupt_id),
            Graph.values(hook_result=hook_result),
            scope=tuple(str(segment) for segment in interrupt.scope),
        )


__all__ = ["ActNode"]

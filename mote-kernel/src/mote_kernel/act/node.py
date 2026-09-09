"""Assembly owner for the public Act tool-use graph."""

from __future__ import annotations

from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.authorize import AuthorizeNode
from mote_kernel.act.config import ActBinding, AuthorizeBinding
from mote_kernel.act.contract import (
    ActContractError,
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
    Allow,
    AuthorizationDecision,
    AuthorizationInterruptView,
    Deny,
    HookStateProjection,
    OpaqueGraphFailureReason,
    ResolveStageValue,
    ResumedAuthorization,
)
from mote_kernel.act.execute import ExecuteNode
from mote_kernel.act.failover import (
    ActFailoverDecorators,
    FailoverPortDecorator,
    normalize_act_failover_decorators,
)
from mote_kernel.act.identity import ActHookStage, ActNodeId, ActSlotId, ActValueName
from mote_kernel.act.port import (
    AuthorizePort,
    ExecutePort,
    ResolvePort,
    SettlementPort,
    ToolExchangeWriter,
    capture_authorize_port_contract,
    require_act_port_contracts,
)
from mote_kernel.act.resolve import ResolveNode
from mote_kernel.act.settle import SettleNode
from mote_kernel.config import Config, ConfigActivation, ConfigSnapshotKey, require_config
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import NodeOutputRef
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookPayloadAdmission, HookResult
from mote_kernel.hooks.failover import HookFailoverDecorator, HookFailoverDecorators
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
        "_assembly_snapshot_key",
        "_authorize_port",
        "_failover",
        "_hook",
    )

    @classmethod
    def from_config(
        cls,
        config: Config,
        /,
        *,
        failover: ActFailoverDecorators | FailoverPortDecorator | None = None,
        hook_failover: HookFailoverDecorators[
            PriorityConfigT,
            ActHookEnvelope,
            HookStateT,
            HookCommandT,
        ]
        | HookFailoverDecorator
        | None = None,
    ) -> ActNode[PriorityConfigT, HookStateT, HookCommandT]:
        """Assemble Act from the complete config through its own projection."""

        config = require_config(config)
        selected = config.bind(ActBinding[HookStateT, HookCommandT]())
        hook: HookNode[PriorityConfigT, ActHookEnvelope, HookStateT, HookCommandT] = HookNode[
            PriorityConfigT,
            ActHookEnvelope,
            HookStateT,
            HookCommandT,
        ].from_config(config, selected.hook_slot, failover=hook_failover)
        return cls(
            str(selected.definition_id),
            version=int(selected.definition_version),
            resolve_port=selected.resolve_port,
            authorize_port=selected.authorize_port,
            execute_port=selected.execute_port,
            settlement_port=selected.settlement_port,
            exchange_writer=selected.exchange_writer,
            hook=hook,
            failure_reason=selected.failure_reason,
            admission=selected.admission,
            failover=failover,
            assembly_snapshot_key=config.snapshot.key,
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
        failover: ActFailoverDecorators | FailoverPortDecorator | None = None,
        assembly_snapshot_key: ConfigSnapshotKey | None = None,
    ) -> None:
        if type(admission) is not ActPayloadAdmission:
            raise ActContractError("ActNode requires an ActPayloadAdmission")
        if assembly_snapshot_key is not None and type(assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ActContractError("ActNode assembly snapshot key is malformed")
        admission.admit_graph_failure_reason(failure_reason)
        decorators = normalize_act_failover_decorators(failover)

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
            or hook_slot.node_id != GraphNodeId(str(ActNodeId.HOOK))
            or hook_slot.stage is not HookStage.AFTER_NODE
        ):
            raise ActContractError("ActNode shared HookSlotId does not match its definition")

        for stage in ActHookStage:
            admission.admit_act_slot(ActSlotId(definition_id, version, str(ActNodeId(stage))))

        # Construct every callable before touching the Graph builder so a
        # failed capability assembly cannot leave a partial definition.
        # Each stage owns the one assembly-time decoration of its initial Port;
        # this also keeps direct stage construction equivalent to parent-graph
        # construction and prevents a wrapper from being applied twice.
        resolve = ResolveNode[HookStateT, HookCommandT](
            resolve_port,
            admission,
            decorators,
            assembly_snapshot_key=assembly_snapshot_key,
        )
        authorize = AuthorizeNode[HookStateT, HookCommandT](
            authorize_port,
            failure_reason,
            admission,
            decorators,
            assembly_snapshot_key=assembly_snapshot_key,
        )
        execute = ExecuteNode[HookStateT, HookCommandT](
            execute_port,
            admission,
            decorators,
            assembly_snapshot_key=assembly_snapshot_key,
        )
        settle = SettleNode[HookStateT, HookCommandT](
            settlement_port,
            exchange_writer,
            admission,
            decorators,
            assembly_snapshot_key=assembly_snapshot_key,
        )
        decorated_resolve_port = resolve.resolve_port
        decorated_authorize_port = authorize.authorize_port
        decorated_execute_port = execute.execute_port
        decorated_settlement_port = settle.settlement_port
        decorated_exchange_writer = settle.exchange_writer
        codec_id, codec_version = require_act_port_contracts(
            decorated_resolve_port,
            decorated_authorize_port,
            decorated_execute_port,
            decorated_settlement_port,
            decorated_exchange_writer,
            authorize_codec_binding=authorize.codec_binding,
            authorize_codec_capture=authorize.codec_capture,
        )

        super().__init__(definition_id, version=version)
        self._assembly_snapshot_key = assembly_snapshot_key
        self._authorize_port = decorated_authorize_port
        self._failover = decorators
        self._hook = hook
        self._admission = admission

        request_binding = Graph.bind(
            ActValueName.REQUEST,
            Graph.graph_input(ActValueName.REQUEST, ActRequest),
        )

        # Authorize owns the sole codec/correlation capability.  Install it
        # exactly once on this Graph; Graph remains authoritative for resume
        # scope, state, interrupt and codec admission.
        self.set_resume_codec(
            codec_id,
            codec_version,
            decorated_authorize_port.encode_graph_input,
            decorated_authorize_port.decode_graph_input,
        )

        resolve_output = self.add_node(
            ActNodeId.RESOLVE,
            resolve,
            inputs=(request_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(request_binding),
                values.activation_config,
            ),
            output_name=ActValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ActNodeId.HOOK,
            hook,
            inputs={ActValueName.REQUEST: Graph.node_output(resolve_output)},
        )
        # Resolve the nested Hook's declared boundary through the generic
        # Graph API.  This preserves the child descriptor identity for every
        # downstream typed binding and for this graph's public output.
        hook_result_ref = cast(
            NodeOutputRef[HookResult[ActHookEnvelope, HookCommandT]],
            self.output_ref(ActNodeId.HOOK, ActValueName.RESULT),
        )
        authorize_hook_result_binding = Graph.bind(ActValueName.HOOK_RESULT, hook_result_ref)
        stage_hook_result_binding = Graph.bind(
            ActValueName.HOOK_RESULT,
            Graph.node_output(hook_result_ref),
        )
        self.add_node(
            ActNodeId.AUTHORIZE,
            authorize,
            # The resume boundary may provide an explicit authorization input;
            # bind normal activations to the most recent Hook publication
            # selected by the compiler.  A predecessor-only binding cannot be
            # resumed with an override, so the fixed Hook source is required
            # for this interruptible stage.
            inputs=(authorize_hook_result_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(authorize_hook_result_binding),
                values.activation_config,
            ),
            output_name=ActValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ActNodeId.EXECUTE,
            execute,
            inputs=(stage_hook_result_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(stage_hook_result_binding),
                values.activation_config,
            ),
            output_name=ActValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ActNodeId.SETTLE,
            settle,
            inputs=(stage_hook_result_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(stage_hook_result_binding),
                values.activation_config,
            ),
            output_name=ActValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_edge(Graph.START, ActNodeId.RESOLVE)
        for business_node in (
            ActNodeId.RESOLVE,
            ActNodeId.AUTHORIZE,
            ActNodeId.EXECUTE,
            ActNodeId.SETTLE,
        ):
            self.add_edge(business_node, ActNodeId.HOOK)
        # Hook returns the current business node identity as its terminal
        # route.  Act owns the mapping from that identity to its next stage.
        self.add_edge(ActNodeId.HOOK, ActNodeId.RESOLVE, ActNodeId.AUTHORIZE)
        self.add_edge(ActNodeId.HOOK, ActNodeId.AUTHORIZE, ActNodeId.EXECUTE)
        self.add_edge(ActNodeId.HOOK, ActNodeId.EXECUTE, ActNodeId.SETTLE)
        self.add_edge(ActNodeId.HOOK, ActNodeId.SETTLE, Graph.END)
        self.set_outputs({ActValueName.RESULT: hook_result_ref})

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
        activation_config: Config | None = None,
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
        authorize_port = self._authorize_port
        if activation_config is not None:
            selected = activation_config.bind(AuthorizeBinding[HookStateT, HookCommandT]())
            if selected.admission != self._admission:
                raise ActContractError("Act config binding changed the compiled payload contract")
            if self._assembly_snapshot_key is None or selected.snapshot_key != self._assembly_snapshot_key:
                authorize_port = self._failover.authorize_port(selected.capability.port)
                capture_authorize_port_contract(authorize_port)
        resumed = authorize_port.build_resume_input(view, decision)
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
            GraphNodeId(str(ActNodeId.RESOLVE)),
        )
        self._admission.admit_hook_result(hook_result)
        return self.resume_interrupted(
            ActNodeId.AUTHORIZE,
            str(interrupt.interrupt_id),
            Graph.values(**{ActValueName.HOOK_RESULT: hook_result}),
            scope=tuple(str(segment) for segment in interrupt.scope),
        )


__all__ = ["ActNode"]

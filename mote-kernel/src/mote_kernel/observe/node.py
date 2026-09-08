"""Observe graph assembly and its two state-bearing business nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.config import Config, ConfigActivation, require_config
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import GraphInputRef, TypedInputBinding
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookPayloadAdmission, HookResult
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.config import (
    AcknowledgeBinding,
    GetObservationBinding,
    ObserveBinding,
    WriteObservationBinding,
)
from mote_kernel.observe.contract import (
    AssistantBatch,
    Available,
    BackgroundTaskSnapshot,
    ConfigApplyResult,
    ConfigSettlementReceipt,
    Conflict,
    ContextAppendReceipt,
    DeliveryAck,
    Empty,
    GetObservationStageValue,
    HookStateProjection,
    ObservationBatch,
    ObservationBatchReceipt,
    ObserveContractError,
    ObserveFrame,
    ObserveHookCommand,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    ToolBatch,
    UserBatch,
    WriteObservationStageValue,
    observation_kind,
    validate_settlement_boundary,
)
from mote_kernel.observe.identity import (
    DeliveryAckReference,
    ObservationBoundary,
    ObservationWait,
    ObserveHookStage,
    ObserveIdentityError,
    ObserveNodeId,
    ObserveValueName,
)
from mote_kernel.observe.port import (
    BackgroundTaskPort,
    ConfigObservationPort,
    ContextObservationPort,
    ObservationAckPort,
    ObservationQueuePort,
    ObservationResumePort,
    require_observe_port_contracts,
)
from mote_kernel.state.graph_state import GraphDefinitionId, GraphNodeId

PriorityConfigT = TypeVar("PriorityConfigT")
HookStateT = TypeVar("HookStateT", bound=HookStateProjection)
HookCommandT = TypeVar("HookCommandT", bound=ObserveHookCommand)


def _conflict_reason(conflict: Conflict, /) -> str:
    families = ",".join(family.value for family in conflict.conflict.families)
    return f"observe batch contains mutually exclusive non-config families: {families}"


@dataclass(frozen=True, slots=True)
class GetObservationNode(Generic[HookStateT, HookCommandT]):
    """Read one complete queue window and publish the first Hook envelope."""

    queue_port: ObservationQueuePort
    background_task_port: BackgroundTaskPort
    admission: ObservePayloadAdmission

    async def __call__(
        self,
        value: ConfigActivation[ObserveRequest[HookStateT]],
        /,
    ) -> HookActivationRequest[ObserveHookEnvelope, HookStateT] | Graph.Outcome[HookGraphValue]:
        request = self.admission.admit_request(value.value)
        activation_config = value.activation_config
        queue_port = self.queue_port
        background_task_port = self.background_task_port
        if activation_config is not None:
            selected = activation_config.bind(GetObservationBinding())
            if selected.admission != self.admission:
                raise ObserveContractError("Observe config binding changed the compiled payload contract")
            queue_port = selected.capability.queue_port
            background_task_port = selected.capability.background_task_port
        read = self.admission.admit_read_after(await queue_port.read_after(request.cursor), request.cursor)
        if type(read) is Empty:
            # The provider performs an atomic recheck/registration.  The
            # registration receipt is intentionally not treated as a message;
            # the durable wait coordinate is the only interrupt payload.
            registration = await queue_port.register_wait(read.wait)
            self.admission.admit_wait_registration_for(read.wait, registration)
            return Graph.interrupt(read.wait.encode())
        if type(read) is Conflict:
            return Graph.failure(_conflict_reason(read))
        available = cast(Available, read)
        snapshot = self.admission.admit_task_snapshot(await background_task_port.snapshot(available.boundary))
        if snapshot.observation_revision != available.boundary.observation_revision:
            raise ObserveContractError("background task snapshot revision does not match observation boundary")
        frame = self.admission.admit_frame(ObserveFrame(available.batch, available.boundary, snapshot))
        envelope = ObserveHookEnvelope(
            ObserveHookStage.AFTER_GET_OBSERVATION,
            GetObservationStageValue(frame),
            request.hook_state,
        )
        hook_request = HookActivationRequest(
            envelope,
            request.hook_state,
            GraphNodeId(str(ObserveNodeId.GET_OBSERVATION)),
        )
        self.admission.admit_hook_request(hook_request)
        return hook_request


def _settlement_id(
    boundary: ObservationBoundary,
    config_receipt: ConfigSettlementReceipt | None,
    context_receipt: ContextAppendReceipt | None,
    /,
) -> str:
    parts = ["mote.observe.settlement.v1", boundary.stream_id, str(boundary.cursor_after.sequence)]
    if config_receipt is not None:
        parts.extend(("config", config_receipt.settlement_id))
    if context_receipt is not None:
        parts.extend(("context", context_receipt.settlement_id))
    settlement_id = "".join(f"{len(part.encode('utf-8'))}:{part}" for part in parts)
    encoded_length = len(settlement_id.encode("utf-8"))
    if encoded_length > 256:
        raise ObserveContractError("observation settlement identity exceeds its 256-byte limit")
    return settlement_id


def _snapshot_for_settlement(
    frame: ObserveFrame,
    boundary: ObservationBoundary,
    config_receipt: ConfigSettlementReceipt | None,
    context_receipt: ContextAppendReceipt | None,
    /,
) -> BackgroundTaskSnapshot:
    supplied = tuple(
        receipt.background_task_snapshot
        for receipt in (config_receipt, context_receipt)
        if receipt is not None and receipt.background_task_snapshot is not None
    )
    if supplied and any(candidate != supplied[0] for candidate in supplied[1:]):
        raise ObserveContractError("settlement returned conflicting task snapshots")
    selected = supplied[0] if supplied else frame.background_task_snapshot
    if boundary == frame.boundary and selected != frame.background_task_snapshot:
        raise ObserveContractError("settlement changed the task snapshot without advancing its boundary")
    return selected


@dataclass(frozen=True, slots=True)
class WriteObservationNode(Generic[HookStateT, HookCommandT]):
    """Settle Config/Context values and publish the final Hook envelope."""

    config_port: ConfigObservationPort
    context_port: ContextObservationPort
    admission: ObservePayloadAdmission

    async def __call__(
        self,
        activation: ConfigActivation[HookResult[ObserveHookEnvelope, HookCommandT]],
        /,
    ) -> ConfigActivation[HookActivationRequest[ObserveHookEnvelope, HookStateT]]:
        value = activation.value
        hook_result = self.admission.admit_hook_result(value)
        envelope = hook_result.value
        if (
            envelope.stage is not ObserveHookStage.AFTER_GET_OBSERVATION
            or type(envelope.payload) is not GetObservationStageValue
        ):
            raise ObserveContractError("write_observation input must be an after-get Hook envelope")
        frame = envelope.payload.frame
        batch = frame.batch
        config_apply_result: ConfigApplyResult | None = None
        config_receipt: ConfigSettlementReceipt | None = None
        context_receipt: ContextAppendReceipt | None = None
        current_config = activation.activation_config
        config_port = self.config_port
        context_port = self.context_port
        if current_config is not None:
            selected = current_config.bind(WriteObservationBinding())
            if selected.admission != self.admission:
                raise ObserveContractError("Observe config binding changed the compiled payload contract")
            config_port = selected.capability.config_port
            context_port = selected.capability.context_port
        config = batch.config
        if config is not None:
            config_batch = self.admission.admit_config_batch(config)
            config_apply_result = self.admission.admit_config_apply_result(await config_port.apply(config_batch))
            config_receipt = config_apply_result.receipt
            if config_receipt.read_boundary != frame.boundary:
                raise ObserveContractError("Config settlement receipt read boundary does not match observation frame")
            if config_receipt.delivery_ids != config_batch.delivery_ids:
                raise ObserveContractError("Config settlement receipt does not cover the complete Config batch")
        context_batch = _context_batch(batch)
        if context_batch is not None:
            self.admission.admit_context_batch(context_batch)
            context_receipt = self.admission.admit_context_receipt(await context_port.append(context_batch))
            if context_receipt.read_boundary != frame.boundary:
                raise ObserveContractError("Context settlement receipt read boundary does not match observation frame")
            if context_receipt.delivery_ids != context_batch.delivery_ids:
                raise ObserveContractError("Context settlement receipt does not cover the complete Context batch")
            if context_receipt.family is not batch.non_config_families[0]:
                raise ObserveContractError("Context settlement receipt family does not match observation batch")
        settlement_boundaries = tuple(
            receipt.settlement_boundary for receipt in (config_receipt, context_receipt) if receipt is not None
        )
        for candidate in settlement_boundaries:
            # Keep the relation in the contract owner so direct receipt
            # admission and this live write path cannot drift apart.
            validate_settlement_boundary(frame.boundary, candidate, "observation")
        # Config and Context are separate capability calls, but their receipts
        # describe one Observe activation.  A single successor boundary is
        # therefore required whenever both are present; selecting the newer
        # one would make the other receipt impossible to replay atomically.
        if settlement_boundaries and any(
            candidate != settlement_boundaries[0] for candidate in settlement_boundaries[1:]
        ):
            raise ObserveContractError("Config and Context receipts disagree on settlement boundary")
        settlement_boundary = settlement_boundaries[0] if settlement_boundaries else frame.boundary
        snapshot = _snapshot_for_settlement(frame, settlement_boundary, config_receipt, context_receipt)
        ack_reference = DeliveryAckReference(
            frame.boundary.stream_id,
            batch.delivery_ids,
            _settlement_id(settlement_boundary, config_receipt, context_receipt),
        )
        receipt = ObservationBatchReceipt(
            frame.boundary,
            settlement_boundary,
            config_receipt,
            context_receipt,
            ack_reference,
        )
        successor_config = current_config
        if config_apply_result is not None and config_apply_result.successor_config is not None:
            candidate = config_apply_result.successor_config
            if current_config is not None:
                current_key = current_config.snapshot.key
                candidate_key = candidate.snapshot.key
                if (
                    candidate_key.definition_id != current_key.definition_id
                    or candidate_key.definition_version != current_key.definition_version
                    or candidate_key.revision != current_key.revision + 1
                ):
                    raise ObserveContractError(
                        "Config successor must preserve graph identity and advance its snapshot revision"
                    )
            successor_config = candidate
        result = ObserveResult(
            observation_kind(batch),
            batch.delivery_ids,
            settlement_boundary.cursor_range,
            snapshot,
            receipt,
        )
        hook_state = cast(HookStateT, envelope.hook_state)
        next_envelope = ObserveHookEnvelope(
            ObserveHookStage.AFTER_WRITE_OBSERVATION,
            WriteObservationStageValue(result),
            hook_state,
        )
        hook_request = HookActivationRequest(
            next_envelope,
            hook_state,
            GraphNodeId(str(ObserveNodeId.WRITE_OBSERVATION)),
        )
        self.admission.admit_hook_request(hook_request)
        return ConfigActivation(hook_request, successor_config)


def _context_batch(batch: ObservationBatch, /) -> ToolBatch | UserBatch | AssistantBatch | None:
    # ``ObservationBatch`` exposes exactly one non-Config family after the
    # queue admission check.  The explicit branches retain nominal classes at
    # the Port boundary and avoid a union payload or a reflective lookup.
    tool = batch.tool
    if tool is not None:
        return tool
    user = batch.user
    if user is not None:
        return user
    assistant = batch.assistant
    if assistant is not None:
        return assistant
    return None


class ObserveNode(
    Graph[HookGraphValue],
    Generic[PriorityConfigT, HookStateT, HookCommandT],
):
    """The two-business-node Observe nested graph with one shared Hook."""

    __slots__ = (
        "_ack_port",
        "_admission",
        "_hook",
    )

    @classmethod
    def from_config(
        cls,
        config: Config,
        /,
    ) -> ObserveNode[PriorityConfigT, HookStateT, HookCommandT]:
        """Assemble Observe from the complete config via its own projection.

        Observe receives the complete object only at this assembly boundary;
        all capabilities retained by the node come from ``ObserveBinding``.
        Its shared Hook is independently selected by the Hook slot declared in
        that projection.
        """

        config = require_config(config)
        selected = config.bind(ObserveBinding[PriorityConfigT, HookStateT, HookCommandT]())
        hook: HookNode[PriorityConfigT, ObserveHookEnvelope, HookStateT, HookCommandT] = HookNode[
            PriorityConfigT,
            ObserveHookEnvelope,
            HookStateT,
            HookCommandT,
        ].from_config(config, selected.hook_slot)
        return cls(
            str(selected.definition_id),
            version=int(selected.definition_version),
            queue_port=selected.queue_port,
            background_task_port=selected.background_task_port,
            config_port=selected.config_port,
            context_port=selected.context_port,
            ack_port=selected.ack_port,
            resume_port=selected.resume_port,
            hook=hook,
            admission=selected.admission,
        )

    def __init__(
        self,
        definition_id: str,
        *,
        version: int = 1,
        queue_port: ObservationQueuePort,
        background_task_port: BackgroundTaskPort,
        config_port: ConfigObservationPort,
        context_port: ContextObservationPort,
        ack_port: ObservationAckPort,
        resume_port: ObservationResumePort,
        hook: HookNode[
            PriorityConfigT,
            ObserveHookEnvelope,
            HookStateT,
            HookCommandT,
        ],
        admission: ObservePayloadAdmission,
    ) -> None:
        if type(admission) is not ObservePayloadAdmission:
            raise ObserveContractError("ObserveNode requires an ObservePayloadAdmission")
        if (
            type(definition_id) is not str
            or not definition_id
            or definition_id != definition_id.strip()
            or "\n" in definition_id
            or "\r" in definition_id
        ):
            raise ObserveContractError("ObserveNode definition_id must be a canonical string")
        if type(version) is not int or version < 1:
            raise ObserveContractError("ObserveNode version must be a positive integer")
        resume_binding = require_observe_port_contracts(
            queue_port,
            background_task_port,
            config_port,
            context_port,
            ack_port,
            resume_port,
        )
        if type(hook) is not HookNode:
            raise ObserveContractError("ObserveNode requires one shared HookNode")
        hook_admission = hook.payload_admission
        if type(hook_admission) is not HookPayloadAdmission:
            raise ObserveContractError("Observe shared Hook must expose a HookPayloadAdmission")
        if hook_admission.value_type is not ObserveHookEnvelope:
            raise ObserveContractError("Observe shared Hook value type must be ObserveHookEnvelope")
        if hook_admission.state_type is not admission.hook_state_type:
            raise ObserveContractError("Observe shared Hook state type does not match Observe admission")
        if hook_admission.command_type is not admission.hook_command_type:
            raise ObserveContractError("Observe shared Hook command type does not match Observe admission")
        if hook_admission.transition_admission is not admission:
            raise ObserveContractError("Observe shared Hook must use its ObservePayloadAdmission for transitions")
        hook_slot = hook.slot
        if type(hook_slot) is not HookSlotId:
            raise ObserveContractError("Observe shared Hook must expose a HookSlotId")
        if (
            hook_slot.definition_id != GraphDefinitionId(definition_id)
            or int(hook_slot.definition_version) != version
            or hook_slot.node_id != GraphNodeId(str(ObserveNodeId.HOOK))
            or hook_slot.stage is not HookStage.AFTER_NODE
        ):
            raise ObserveContractError("Observe shared HookSlotId does not match its definition")
        admission.admit_observe_slot(hook_slot)

        # Build all callable nodes before touching the parent Graph builder;
        # failed capability assembly cannot leave a partial definition.
        get_node = GetObservationNode[HookStateT, HookCommandT](
            queue_port,
            background_task_port,
            admission,
        )
        write_node = WriteObservationNode[HookStateT, HookCommandT](
            config_port,
            context_port,
            admission,
        )

        super().__init__(definition_id, version=version)
        self._ack_port = ack_port
        self._hook = hook
        self._admission = admission

        request_input = cast(
            GraphInputRef[ObserveRequest[HookStateT]],
            Graph.graph_input(ObserveValueName.REQUEST, ObserveRequest),
        )
        request_binding = Graph.bind(ObserveValueName.REQUEST, request_input)

        self.set_resume_codec(
            resume_binding.codec_id,
            resume_binding.codec_version,
            resume_binding.encoder,
            resume_binding.decoder,
        )

        get_output = self.add_node(
            ObserveNodeId.GET_OBSERVATION,
            get_node,
            inputs=(request_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(request_binding),
                values.activation_config,
            ),
            output_name=ObserveValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ObserveNodeId.HOOK,
            hook,
            inputs={ObserveValueName.REQUEST: Graph.node_output(get_output)},
        )
        hook_result_ref = self.output_ref(ObserveNodeId.HOOK, ObserveValueName.RESULT)
        hook_result_source = Graph.node_output(hook_result_ref)
        hook_result_binding = cast(
            TypedInputBinding[HookResult[ObserveHookEnvelope, HookCommandT]],
            Graph.bind(ObserveValueName.HOOK_RESULT, hook_result_source),
        )
        self.add_node(
            ObserveNodeId.WRITE_OBSERVATION,
            write_node,
            inputs=(hook_result_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(hook_result_binding),
                values.activation_config,
            ),
            output_name=ObserveValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_edge(Graph.START, ObserveNodeId.GET_OBSERVATION)
        self.add_edge(ObserveNodeId.GET_OBSERVATION, ObserveNodeId.HOOK)
        self.add_edge(
            ObserveNodeId.HOOK,
            ObserveNodeId.GET_OBSERVATION,
            ObserveNodeId.WRITE_OBSERVATION,
        )
        self.add_edge(ObserveNodeId.WRITE_OBSERVATION, ObserveNodeId.HOOK)
        self.add_edge(ObserveNodeId.HOOK, ObserveNodeId.WRITE_OBSERVATION, Graph.END)
        self.set_outputs({ObserveValueName.RESULT: hook_result_ref})

    @property
    def hook(
        self,
    ) -> HookNode[
        PriorityConfigT,
        ObserveHookEnvelope,
        HookStateT,
        HookCommandT,
    ]:
        """Return the one shared HookNode installed during assembly."""

        return self._hook

    async def acknowledge(
        self,
        result: ObserveResult,
        /,
        *,
        activation_config: Config | None = None,
    ) -> DeliveryAck:
        """Acknowledge a settled result after its enclosing commit succeeds."""

        admitted = self._admission.admit_result(result)
        ack_port = self._ack_port
        if activation_config is not None:
            selected = activation_config.bind(AcknowledgeBinding())
            if selected.admission != self._admission:
                raise ObserveContractError("Observe config binding changed the compiled payload contract")
            ack_port = selected.capability
        ack = self._admission.admit_ack(await ack_port.acknowledge(admitted.delivery_ids, admitted.observation_receipt))
        if ack.reference != admitted.observation_receipt.ack_reference:
            raise ObserveContractError("delivery acknowledgement does not match observation receipt")
        return ack

    def resume_observation(
        self,
        *,
        awaiting: Graph.AwaitingResumeResult[HookGraphValue],
        interrupt_id: str,
        hook_state: HookStateT,
    ) -> Graph.ResumeAction[HookGraphValue]:
        """Build a typed wake resume using the wait cursor carried by the interrupt.

        A wake notification is not a business delivery.  Parsing its durable
        ``ObservationWait`` payload here forces the resumed request to reread
        from the provider-owned ``after_cursor`` and makes it impossible for a
        caller to accidentally reuse the pre-wait cursor.
        """

        matches = tuple(
            interrupt
            for interrupt in awaiting.interrupts
            if str(interrupt.interrupt_id) == interrupt_id and str(interrupt.node_id) == ObserveNodeId.GET_OBSERVATION
        )
        if len(matches) != 1:
            raise ObserveContractError("interrupt_id must identify exactly one Observe get_observation interrupt")
        interrupt = matches[0]
        try:
            wait = ObservationWait.decode(interrupt.request_payload)
        except ObserveIdentityError as error:
            raise ObserveContractError("Observe interrupt payload is not a valid ObservationWait") from error
        request = self._admission.admit_request(ObserveRequest(wait.after_cursor, hook_state))
        values = Graph.values(**{ObserveValueName.REQUEST: request})
        return self.resume_interrupted(
            ObserveNodeId.GET_OBSERVATION,
            interrupt_id,
            values,
            scope=tuple(str(segment) for segment in interrupt.scope),
        )


__all__ = ["ObserveNode"]

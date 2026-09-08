"""Fail-closed structural admission for Observe values and Hook hand-offs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, TypeVar, cast

from mote_kernel.config import require_config
from mote_kernel.hooks.contract import (
    HookActivationRequest,
    HookGraphValue,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId
from mote_kernel.observe.contract import (
    AssistantBatch,
    AssistantObservation,
    Available,
    BackgroundTaskSnapshot,
    ConfigApplyResult,
    ConfigBatch,
    ConfigObservation,
    ConfigSettlementReceipt,
    Conflict,
    ContextAppendReceipt,
    ContextObservationBatch,
    DeliveryAck,
    Empty,
    GetObservationStageValue,
    HookStateProjection,
    Observation,
    ObservationBatch,
    ObservationBatchReceipt,
    ObservationConflict,
    ObservationDelivery,
    ObservationPayload,
    ObservationRead,
    ObserveContractError,
    ObserveFrame,
    ObserveHookCommand,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    ObserveStageValue,
    ToolBatch,
    ToolObservation,
    UserBatch,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.identity import (
    BlockingTaskRef,
    CursorRange,
    DeliveryAckReference,
    DeliveryId,
    ObservationBoundary,
    ObservationCursor,
    ObservationWait,
    ObserveHookStage,
    WaitRegistration,
)
from mote_kernel.state.graph_state import GraphNodeId

AdmissionValueT = TypeVar("AdmissionValueT")
AdmissionHookStateT = TypeVar("AdmissionHookStateT", bound=HookStateProjection)
AdmissionHookCommandT = TypeVar("AdmissionHookCommandT", bound=ObserveHookCommand)
RevalidatedT = TypeVar("RevalidatedT")


def _exact(value: AdmissionValueT, expected: type[AdmissionValueT], field_name: str, /) -> None:
    if type(value) is not expected:
        raise ObserveContractError(f"{field_name} must be an exact {expected.__name__}")


def _revalidate(factory: Callable[[], RevalidatedT], field_name: str, /) -> RevalidatedT:
    """Re-run a DTO's constructor so forged storage values fail uniformly.

    Providers cross this boundary asynchronously.  A value can therefore be
    structurally forged (for example by a deserializer bypassing
    ``__post_init__``); accessing a missing nested field must never leak an
    ``AttributeError`` or ``TypeError`` from the node.
    """

    try:
        return factory()
    except ObserveContractError:
        raise
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
        raise ObserveContractError(f"{field_name} is malformed") from error


def _concrete_state(value: HookGraphValue, expected: type[HookStateProjection], field_name: str, /) -> None:
    if not isinstance(value, HookStateProjection) or type(value) is HookStateProjection:
        raise ObserveContractError(f"{field_name} must be a concrete HookStateProjection")
    if type(value) is not expected:
        raise ObserveContractError(f"{field_name} has an unexpected concrete state type")


def _concrete_command(value: HookGraphValue, expected: type[ObserveHookCommand], field_name: str, /) -> None:
    if not isinstance(value, ObserveHookCommand) or type(value) is ObserveHookCommand:
        raise ObserveContractError(f"{field_name} must be a concrete ObserveHookCommand")
    if type(value) is not expected:
        raise ObserveContractError(f"{field_name} has an unexpected concrete command type")


def _is_concrete_subclass(candidate: type[HookGraphValue], base: type[HookGraphValue], /) -> bool:
    try:
        return issubclass(candidate, base) and candidate is not base
    except TypeError:
        # Dataclass annotations are not runtime guards.  A composition root
        # may receive a forged value from configuration/deserialization; keep
        # that failure in Observe's contract vocabulary instead of leaking
        # ``issubclass``'s implementation exception.
        return False


@dataclass(frozen=True, slots=True)
class ObservePayloadAdmission:
    """The one runtime admission contract used by all Observe nodes.

    One Observe definition binds its Hook state/command classes and the exact
    immutable payload class accepted for each observation family.  There is
    no unbound mode: every ingress, Hook hand-off, and settlement reuses this
    same contract.
    """

    hook_state_type: type[HookStateProjection]
    hook_command_type: type[ObserveHookCommand]
    config_payload_type: type[ObservationPayload]
    tool_payload_type: type[ObservationPayload]
    user_payload_type: type[ObservationPayload]
    assistant_payload_type: type[ObservationPayload]

    IDENTITY_MAX_BYTES: ClassVar[int] = 256
    DELIVERY_MAX_COUNT: ClassVar[int] = 4_096

    def __post_init__(self) -> None:
        for value, base, field_name in (
            (self.hook_state_type, HookStateProjection, "hook_state_type"),
            (self.hook_command_type, ObserveHookCommand, "hook_command_type"),
            (self.config_payload_type, ObservationPayload, "config_payload_type"),
            (self.tool_payload_type, ObservationPayload, "tool_payload_type"),
            (self.user_payload_type, ObservationPayload, "user_payload_type"),
            (self.assistant_payload_type, ObservationPayload, "assistant_payload_type"),
        ):
            if not _is_concrete_subclass(value, base):
                raise ObserveContractError(f"{field_name} must be a concrete subclass")

    def admit_observe_slot(self, value: HookSlotId, /) -> HookSlotId:
        _exact(value, HookSlotId, "Observe Hook slot")
        _revalidate(
            lambda: HookSlotId(
                value.definition_id,
                value.definition_version,
                value.node_id,
                value.stage,
            ),
            "Observe Hook slot",
        )
        return value

    def admit_cursor(self, value: ObservationCursor, /) -> ObservationCursor:
        _exact(value, ObservationCursor, "observation cursor")
        _revalidate(
            lambda: ObservationCursor(value.stream_id, value.sequence),
            "observation cursor",
        )
        return value

    def admit_boundary(self, value: ObservationBoundary, /) -> ObservationBoundary:
        _exact(value, ObservationBoundary, "observation boundary")
        _revalidate(
            lambda: ObservationBoundary(
                value.stream_id,
                value.cursor_before,
                value.cursor_after,
                value.observation_revision,
            ),
            "observation boundary",
        )
        self.admit_cursor(value.cursor_before)
        self.admit_cursor(value.cursor_after)
        return value

    def admit_wait(self, value: ObservationWait, /) -> ObservationWait:
        _exact(value, ObservationWait, "observation wait")
        _revalidate(
            lambda: ObservationWait(
                value.stream_id,
                value.after_cursor,
                value.observation_revision,
                value.wake_condition,
            ),
            "observation wait",
        )
        self.admit_cursor(value.after_cursor)
        return value

    def admit_wait_registration(self, value: WaitRegistration, /) -> WaitRegistration:
        _exact(value, WaitRegistration, "wait registration")
        _revalidate(
            lambda: WaitRegistration(
                value.wait_id,
                value.stream_id,
                value.after_cursor,
                value.registration_revision,
            ),
            "wait registration",
        )
        self.admit_cursor(value.after_cursor)
        return value

    def admit_wait_registration_for(
        self,
        wait: ObservationWait,
        registration: WaitRegistration,
        /,
    ) -> WaitRegistration:
        """Admit a provider's atomic registration against its requested wait."""

        self.admit_wait(wait)
        self.admit_wait_registration(registration)
        if (
            registration.stream_id != wait.stream_id
            or registration.after_cursor != wait.after_cursor
            or registration.registration_revision != wait.observation_revision
        ):
            raise ObserveContractError("wait registration does not match its observation wait")
        return registration

    def admit_cursor_range(self, value: CursorRange, /) -> CursorRange:
        _exact(value, CursorRange, "cursor range")
        _revalidate(
            lambda: CursorRange(value.before, value.after),
            "cursor range",
        )
        self.admit_cursor(value.before)
        self.admit_cursor(value.after)
        return value

    def admit_blocking_task(self, value: BlockingTaskRef, /) -> BlockingTaskRef:
        _exact(value, BlockingTaskRef, "blocking task reference")
        _revalidate(
            lambda: BlockingTaskRef(value.task_id, value.incarnation, value.completion_policy),
            "blocking task reference",
        )
        return value

    def admit_ack_reference(self, value: DeliveryAckReference, /) -> DeliveryAckReference:
        _exact(value, DeliveryAckReference, "delivery ack reference")
        _revalidate(
            lambda: DeliveryAckReference(value.stream_id, value.delivery_ids, value.settlement_id),
            "delivery ack reference",
        )
        if len(value.delivery_ids) > self.DELIVERY_MAX_COUNT:
            raise ObserveContractError("delivery ack reference exceeds its delivery limit")
        for delivery_id in value.delivery_ids:
            self.admit_delivery_id(delivery_id)
        return value

    def admit_delivery_id(self, value: DeliveryId, /) -> DeliveryId:
        _exact(value, DeliveryId, "delivery id")
        _revalidate(lambda: DeliveryId(value.value), "delivery id")
        return value

    def admit_observation(self, value: Observation, /) -> Observation:
        if type(value) is ConfigObservation:
            observation = cast(ConfigObservation[HookGraphValue], value)
            _revalidate(
                lambda: cast(Observation, ConfigObservation[HookGraphValue](observation.payload)),
                "Config observation",
            )
            self._admit_payload(observation.payload, self.config_payload_type, "Config observation")
            return cast(Observation, value)
        if type(value) is ToolObservation:
            observation = cast(ToolObservation[HookGraphValue], value)
            _revalidate(
                lambda: cast(Observation, ToolObservation[HookGraphValue](observation.payload)),
                "Tool observation",
            )
            self._admit_payload(observation.payload, self.tool_payload_type, "Tool observation")
            return cast(Observation, value)
        if type(value) is UserObservation:
            observation = cast(UserObservation[HookGraphValue], value)
            _revalidate(
                lambda: cast(Observation, UserObservation[HookGraphValue](observation.payload)),
                "User observation",
            )
            self._admit_payload(observation.payload, self.user_payload_type, "User observation")
            return cast(Observation, value)
        if type(value) is AssistantObservation:
            observation = cast(AssistantObservation[HookGraphValue], value)
            _revalidate(
                lambda: cast(Observation, AssistantObservation[HookGraphValue](observation.payload)),
                "Assistant observation",
            )
            self._admit_payload(observation.payload, self.assistant_payload_type, "Assistant observation")
            return cast(Observation, value)
        raise ObserveContractError("observation must be one of Config, Tool, User, or Assistant")

    @staticmethod
    def _admit_payload(
        value: HookGraphValue,
        expected: type[ObservationPayload],
        field_name: str,
        /,
    ) -> None:
        if type(value) is not expected:
            raise ObserveContractError(f"{field_name} payload has an unexpected concrete type")

    def admit_delivery(self, value: ObservationDelivery[Observation], /) -> ObservationDelivery[Observation]:
        _exact(value, ObservationDelivery, "observation delivery")
        _revalidate(
            lambda: ObservationDelivery(
                value.delivery_id,
                value.cursor_before,
                value.cursor_after,
                value.payload,
                value.observation_revision,
            ),
            "observation delivery",
        )
        self.admit_delivery_id(value.delivery_id)
        self.admit_cursor(value.cursor_before)
        self.admit_cursor(value.cursor_after)
        self.admit_observation(value.payload)
        return value

    def admit_batch(self, value: ObservationBatch, /) -> ObservationBatch:
        _exact(value, ObservationBatch, "observation batch")
        _revalidate(lambda: ObservationBatch(value.deliveries), "observation batch")
        if len(value.deliveries) > self.DELIVERY_MAX_COUNT:
            raise ObserveContractError("observation batch exceeds its delivery limit")
        for delivery in value.deliveries:
            self.admit_delivery(delivery)
        return value

    def admit_config_batch(self, value: ConfigBatch, /) -> ConfigBatch:
        _exact(value, ConfigBatch, "config batch")
        _revalidate(lambda: ConfigBatch(value.deliveries), "config batch")
        self.admit_batch_subset(value.deliveries, ConfigObservation, "config batch")
        return value

    def admit_context_batch(self, value: ContextObservationBatch, /) -> ContextObservationBatch:
        if type(value) not in (ToolBatch, UserBatch, AssistantBatch):
            raise ObserveContractError("context batch must be an exact ToolBatch, UserBatch, or AssistantBatch")
        deliveries: tuple[ObservationDelivery[Observation], ...]
        expected: type[Observation]
        if type(value) is ToolBatch:
            expected = ToolObservation
            _revalidate(lambda: ToolBatch(value.deliveries), "tool batch")
            deliveries = value.deliveries
        elif type(value) is UserBatch:
            expected = UserObservation
            _revalidate(lambda: UserBatch(value.deliveries), "user batch")
            deliveries = value.deliveries
        else:
            expected = AssistantObservation
            assistant_batch = cast(AssistantBatch, value)
            _revalidate(lambda: AssistantBatch(assistant_batch.deliveries), "assistant batch")
            deliveries = assistant_batch.deliveries
        self.admit_batch_subset(deliveries, expected, "context batch")
        return value

    def admit_batch_subset(
        self,
        deliveries: tuple[ObservationDelivery[Observation], ...],
        expected_payload: type[Observation],
        field_name: str,
        /,
    ) -> None:
        if type(deliveries) is not tuple or not deliveries:
            raise ObserveContractError(f"{field_name} deliveries must be a non-empty tuple")
        if len(deliveries) > self.DELIVERY_MAX_COUNT:
            raise ObserveContractError(f"{field_name} exceeds its delivery limit")
        # A provider value may have crossed an asynchronous/deserialization
        # boundary without running the family dataclass constructor.  Rebuild
        # the exact nominal batch here so FIFO, stream/revision, overlap and
        # family invariants are enforced before any Port receives it.
        if expected_payload is ConfigObservation:
            _revalidate(lambda: ConfigBatch(deliveries), field_name)
        elif expected_payload is ToolObservation:
            _revalidate(lambda: ToolBatch(deliveries), field_name)
        elif expected_payload is UserObservation:
            _revalidate(lambda: UserBatch(deliveries), field_name)
        elif expected_payload is AssistantObservation:
            _revalidate(lambda: AssistantBatch(deliveries), field_name)
        else:
            raise ObserveContractError(f"{field_name} has an unknown observation family")
        for delivery in deliveries:
            self.admit_delivery(delivery)

    def admit_delivery_ids(
        self,
        delivery_ids: tuple[DeliveryId, ...],
        field_name: str,
        /,
    ) -> tuple[DeliveryId, ...]:
        """Admit an immutable, distinct delivery-id collection."""

        if type(delivery_ids) is not tuple or not delivery_ids:
            raise ObserveContractError(f"{field_name} must be a non-empty tuple")
        if len(delivery_ids) > self.DELIVERY_MAX_COUNT:
            raise ObserveContractError(f"{field_name} exceeds its delivery limit")
        for delivery_id in delivery_ids:
            self.admit_delivery_id(delivery_id)
        if len(set(delivery_ids)) != len(delivery_ids):
            raise ObserveContractError(f"{field_name} must contain distinct delivery ids")
        return delivery_ids

    def admit_conflict(self, value: ObservationConflict, /) -> ObservationConflict:
        _exact(value, ObservationConflict, "observation conflict")
        _revalidate(lambda: ObservationConflict(value.deliveries, value.families), "observation conflict")
        if len(value.deliveries) > self.DELIVERY_MAX_COUNT:
            raise ObserveContractError("observation conflict exceeds its delivery limit")
        for delivery in value.deliveries:
            self.admit_delivery(delivery)
        return value

    def admit_read(self, value: ObservationRead, /) -> ObservationRead:
        if type(value) is Available:
            _revalidate(lambda: Available(value.batch, value.boundary), "available observation read")
            self.admit_batch(value.batch)
            self.admit_boundary(value.boundary)
            return value
        if type(value) is Empty:
            _revalidate(lambda: Empty(value.wait, value.boundary), "empty observation read")
            self.admit_wait(value.wait)
            self.admit_boundary(value.boundary)
            return value
        if type(value) is Conflict:
            _revalidate(lambda: Conflict(value.conflict, value.boundary), "conflict observation read")
            self.admit_conflict(value.conflict)
            self.admit_boundary(value.boundary)
            return value
        raise ObserveContractError("queue read must be Available, Empty, or Conflict")

    def admit_read_after(
        self,
        value: ObservationRead,
        cursor: ObservationCursor,
        /,
    ) -> ObservationRead:
        """Admit a read result whose boundary starts exactly at ``cursor``."""

        read = self.admit_read(value)
        self.admit_cursor(cursor)
        boundary = read.boundary if type(read) is Available or type(read) is Empty else cast(Conflict, read).boundary
        if boundary.stream_id != cursor.stream_id or boundary.cursor_before != cursor:
            raise ObserveContractError("queue read boundary does not start at the requested cursor")
        return read

    def admit_task_snapshot(self, value: BackgroundTaskSnapshot, /) -> BackgroundTaskSnapshot:
        _exact(value, BackgroundTaskSnapshot, "background task snapshot")
        canonical = _revalidate(
            lambda: BackgroundTaskSnapshot(value.observation_revision, value.blocking_tasks),
            "background task snapshot",
        )
        for task in value.blocking_tasks:
            self.admit_blocking_task(task)
        if value.blocking_tasks != canonical.blocking_tasks:
            raise ObserveContractError("background task snapshot blocking_tasks are not canonical")
        return value

    def admit_request(self, value: ObserveRequest[AdmissionHookStateT], /) -> ObserveRequest[AdmissionHookStateT]:
        _exact(value, ObserveRequest, "Observe request")
        _revalidate(lambda: ObserveRequest(value.cursor, value.hook_state), "Observe request")
        self.admit_cursor(value.cursor)
        _concrete_state(value.hook_state, self.hook_state_type, "Observe request hook_state")
        return value

    def admit_frame(self, value: ObserveFrame, /) -> ObserveFrame:
        _exact(value, ObserveFrame, "Observe frame")
        _revalidate(
            lambda: ObserveFrame(value.batch, value.boundary, value.background_task_snapshot),
            "Observe frame",
        )
        self.admit_batch(value.batch)
        self.admit_boundary(value.boundary)
        self.admit_task_snapshot(value.background_task_snapshot)
        return value

    def admit_config_receipt(self, value: ConfigSettlementReceipt, /) -> ConfigSettlementReceipt:
        _exact(value, ConfigSettlementReceipt, "Config settlement receipt")
        _revalidate(
            lambda: ConfigSettlementReceipt(
                value.delivery_ids,
                value.read_boundary,
                value.settlement_boundary,
                value.settlement_id,
                value.background_task_snapshot,
            ),
            "Config settlement receipt",
        )
        self.admit_delivery_ids(value.delivery_ids, "Config settlement delivery_ids")
        self.admit_boundary(value.read_boundary)
        self.admit_boundary(value.settlement_boundary)
        if value.background_task_snapshot is not None:
            self.admit_task_snapshot(value.background_task_snapshot)
        return value

    def admit_config_apply_result(self, value: ConfigApplyResult, /) -> ConfigApplyResult:
        _exact(value, ConfigApplyResult, "Config apply result")
        _revalidate(
            lambda: ConfigApplyResult(value.receipt, value.successor_config),
            "Config apply result",
        )
        self.admit_config_receipt(value.receipt)
        if value.successor_config is not None:
            require_config(value.successor_config)
        return value

    def admit_context_receipt(self, value: ContextAppendReceipt, /) -> ContextAppendReceipt:
        _exact(value, ContextAppendReceipt, "Context append receipt")
        _revalidate(
            lambda: ContextAppendReceipt(
                value.delivery_ids,
                value.read_boundary,
                value.settlement_boundary,
                value.settlement_id,
                value.family,
                value.background_task_snapshot,
            ),
            "Context append receipt",
        )
        self.admit_delivery_ids(value.delivery_ids, "Context append delivery_ids")
        self.admit_boundary(value.read_boundary)
        self.admit_boundary(value.settlement_boundary)
        if value.background_task_snapshot is not None:
            self.admit_task_snapshot(value.background_task_snapshot)
        return value

    def admit_batch_receipt(self, value: ObservationBatchReceipt, /) -> ObservationBatchReceipt:
        _exact(value, ObservationBatchReceipt, "observation batch receipt")
        _revalidate(
            lambda: ObservationBatchReceipt(
                value.read_boundary,
                value.settlement_boundary,
                value.config_receipt,
                value.context_receipt,
                value.ack_reference,
            ),
            "observation batch receipt",
        )
        if value.config_receipt is not None:
            self.admit_config_receipt(value.config_receipt)
        if value.context_receipt is not None:
            self.admit_context_receipt(value.context_receipt)
        self.admit_boundary(value.read_boundary)
        self.admit_boundary(value.settlement_boundary)
        self.admit_ack_reference(value.ack_reference)
        return value

    def admit_ack(self, value: DeliveryAck, /) -> DeliveryAck:
        _exact(value, DeliveryAck, "delivery ack")
        _revalidate(lambda: DeliveryAck(value.reference), "delivery ack")
        self.admit_ack_reference(value.reference)
        return value

    def admit_result(self, value: ObserveResult, /) -> ObserveResult:
        _exact(value, ObserveResult, "Observe result")
        _revalidate(
            lambda: ObserveResult(
                value.current_state,
                value.delivery_ids,
                value.cursor_range,
                value.background_task_snapshot,
                value.observation_receipt,
            ),
            "Observe result",
        )
        self.admit_cursor_range(value.cursor_range)
        self.admit_batch_receipt(value.observation_receipt)
        self.admit_task_snapshot(value.background_task_snapshot)
        return value

    def admit_stage_value(self, value: ObserveStageValue, /) -> ObserveStageValue:
        if type(value) not in (GetObservationStageValue, WriteObservationStageValue):
            raise ObserveContractError("Observe stage value must be a known concrete stage value")
        if type(value) is GetObservationStageValue:
            _revalidate(lambda: GetObservationStageValue(value.frame), "get-observation stage value")
            self.admit_frame(value.frame)
        else:
            _revalidate(
                lambda: WriteObservationStageValue(cast(WriteObservationStageValue, value).result),
                "write-observation stage value",
            )
            self.admit_result(cast(WriteObservationStageValue, value).result)
        return value

    def admit_hook_envelope(self, value: ObserveHookEnvelope, /) -> ObserveHookEnvelope:
        _exact(value, ObserveHookEnvelope, "Observe Hook envelope")
        _revalidate(
            lambda: ObserveHookEnvelope(value.stage, value.payload, value.hook_state),
            "Observe Hook envelope",
        )
        self.admit_stage_value(value.payload)
        _concrete_state(value.hook_state, self.hook_state_type, "Observe Hook envelope hook_state")
        return value

    def admit_hook_request(
        self,
        value: HookActivationRequest[ObserveHookEnvelope, AdmissionHookStateT],
        /,
    ) -> HookActivationRequest[ObserveHookEnvelope, AdmissionHookStateT]:
        _exact(value, HookActivationRequest, "Observe Hook activation")
        _revalidate(
            lambda: HookActivationRequest(
                value.value,
                value.state,
                value.node_id,
            ),
            "Observe Hook activation",
        )
        self.admit_hook_envelope(value.value)
        _concrete_state(value.state, self.hook_state_type, "Observe Hook request state")
        if value.state != value.value.hook_state:
            raise ObserveContractError("Observe Hook request state does not match its envelope")
        expected = self._expected_hook_node(value.value.stage)
        if value.node_id != expected:
            raise ObserveContractError("Observe Hook request node_id does not match its stage")
        return value

    def admit_hook_result(
        self,
        value: HookResult[ObserveHookEnvelope, AdmissionHookCommandT],
        /,
    ) -> HookResult[ObserveHookEnvelope, AdmissionHookCommandT]:
        _exact(value, HookResult, "Observe Hook result")
        _revalidate(
            lambda: HookResult(value.value, value.commands, value.node_id),
            "Observe Hook result",
        )
        self.admit_hook_envelope(value.value)
        for command in value.commands:
            _concrete_command(command, self.hook_command_type, "Observe Hook command")
        expected = self._expected_hook_node(value.value.stage)
        if value.node_id != expected:
            raise ObserveContractError("Observe Hook result node_id does not match its stage")
        return value

    def admit_transition(
        self,
        request: HookActivationRequest[ObserveHookEnvelope, AdmissionHookStateT],
        result: HookStageResult[ObserveHookEnvelope, AdmissionHookCommandT],
        /,
    ) -> None:
        """Admit one bounded Observe Hook rewrite.

        Hook priorities may replace the stage payload.  The read-only Hook
        state and the business stage remain bound to the activation that
        entered the shared Hook, while ``HookNode`` owns and preserves the
        request ``node_id`` used by the containing graph.
        """

        self.admit_hook_request(request)
        _exact(result, HookStageResult, "Observe Hook stage result")
        self.admit_hook_envelope(result.value)
        if result.value.stage is not request.value.stage:
            raise ObserveContractError("Observe Hook rewrite cannot change its business stage")
        if result.value.hook_state != request.state:
            raise ObserveContractError("Observe Hook rewrite cannot change its read-only state")
        if type(result.commands) is not tuple:
            raise ObserveContractError("Observe Hook stage commands must be a tuple")
        for command in result.commands:
            _concrete_command(command, self.hook_command_type, "Observe Hook stage command")

    @staticmethod
    def _expected_hook_node(stage: ObserveHookStage, /) -> GraphNodeId:
        if stage is ObserveHookStage.AFTER_GET_OBSERVATION:
            return GraphNodeId("get_observation")
        if stage is ObserveHookStage.AFTER_WRITE_OBSERVATION:
            return GraphNodeId("write_observation")
        raise ObserveContractError("Observe Hook stage is unknown")


__all__ = ["ObservePayloadAdmission"]

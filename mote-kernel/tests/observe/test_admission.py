"""Fail-closed admission tests for values returning from Observe Ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never, cast

import pytest
from tests.observe.test_nodes import (
    ObserveTestAssistantPayload as _AssistantPayload,
)
from tests.observe.test_nodes import (
    ObserveTestCommand as _Command,
)
from tests.observe.test_nodes import (
    ObserveTestConfigPayload as _ConfigPayload,
)
from tests.observe.test_nodes import (
    ObserveTestState as _State,
)
from tests.observe.test_nodes import (
    ObserveTestToolPayload as _ToolPayload,
)
from tests.observe.test_nodes import (
    ObserveTestUserPayload as _UserPayload,
)
from tests.observe.test_nodes import (
    make_available as _available,
)
from tests.observe.test_nodes import (
    make_cursor as _cursor,
)
from tests.observe.test_nodes import (
    make_delivery as _delivery,
)
from tests.observe.test_nodes import (
    make_empty as _empty,
)

from mote_kernel.hooks.contract import HookActivationRequest, HookResult, HookStageResult
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.contract import (
    AssistantObservation,
    Available,
    BackgroundTaskSnapshot,
    ConfigBatch,
    ConfigObservation,
    ContextAppendReceipt,
    DeliveryAck,
    DeliveryAckReference,
    GetObservationStageValue,
    HookStateProjection,
    NonConfigObservationFamily,
    ObservationBatch,
    ObservationBatchReceipt,
    ObservationConflict,
    ObservationDelivery,
    ObservationKind,
    ObservationPayload,
    ObserveContractError,
    ObserveFrame,
    ObserveHookCommand,
    ObserveHookEnvelope,
    ObserveResult,
    ToolObservation,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.identity import (
    DeliveryId,
    ObservationBoundary,
    ObserveHookStage,
)
from mote_kernel.state.graph_state import GraphNodeId


@dataclass(frozen=True, slots=True)
class _OtherState(HookStateProjection):
    marker: str = "other"


@dataclass(frozen=True, slots=True)
class _OtherCommand(ObserveHookCommand):
    marker: str = "other"


def _admission() -> ObservePayloadAdmission:
    return ObservePayloadAdmission(
        _State,
        _Command,
        _ConfigPayload,
        _ToolPayload,
        _UserPayload,
        _AssistantPayload,
    )


def _boundary(before: int = 0, after: int = 1, revision: int = 1) -> ObservationBoundary:
    return ObservationBoundary("stream", _cursor(before), _cursor(after), revision)


def _snapshot(revision: int = 1) -> BackgroundTaskSnapshot:
    return BackgroundTaskSnapshot(revision, ())


def _user_frame() -> ObserveFrame:
    available = _available(_delivery(0, UserObservation("user"), "user"))
    return ObserveFrame(available.batch, available.boundary, _snapshot())


def _unknown_hook_stage() -> ObserveHookStage:
    """Build an enum-shaped provider value that is not a declared stage."""

    stage = str.__new__(ObserveHookStage, "unknown")
    object.__setattr__(stage, "_name_", "UNKNOWN")
    object.__setattr__(stage, "_value_", "unknown")
    return stage


def _user_result() -> ObserveResult:
    frame = _user_frame()
    receipt = ObservationBatchReceipt(
        frame.boundary,
        frame.boundary,
        None,
        ContextAppendReceipt(
            frame.batch.delivery_ids,
            frame.boundary,
            frame.boundary,
            "context",
            NonConfigObservationFamily.USER,
        ),
        DeliveryAckReference("stream", frame.batch.delivery_ids, "ack"),
    )
    return ObserveResult(
        ObservationKind.USER,
        frame.batch.delivery_ids,
        frame.boundary.cursor_range,
        frame.background_task_snapshot,
        receipt,
    )


def test_admission_rejects_nonconcrete_configured_hook_types() -> None:
    with pytest.raises(ObserveContractError, match="hook_state_type"):
        ObservePayloadAdmission(
            cast(Never, HookStateProjection),
            _Command,
            _ConfigPayload,
            _ToolPayload,
            _UserPayload,
            _AssistantPayload,
        )
    with pytest.raises(ObserveContractError, match="hook_command_type"):
        ObservePayloadAdmission(
            _State,
            cast(Never, ObserveHookCommand),
            _ConfigPayload,
            _ToolPayload,
            _UserPayload,
            _AssistantPayload,
        )
    with pytest.raises(ObserveContractError, match="concrete subclass"):
        ObservePayloadAdmission(
            cast(Never, object()),
            _Command,
            _ConfigPayload,
            _ToolPayload,
            _UserPayload,
            _AssistantPayload,
        )
    with pytest.raises(ObserveContractError, match="config_payload_type"):
        ObservePayloadAdmission(
            _State,
            _Command,
            cast(Never, ObservationPayload),
            _ToolPayload,
            _UserPayload,
            _AssistantPayload,
        )


@pytest.mark.parametrize(
    "observation",
    [
        ConfigObservation(_ConfigPayload("config")),
        ToolObservation(_ToolPayload("tool")),
        UserObservation(_UserPayload("user")),
        AssistantObservation(_AssistantPayload("assistant")),
    ],
)
def test_admission_accepts_each_exact_bound_payload_class(observation: object) -> None:
    assert _admission().admit_observation(cast(Never, observation)) is observation


@pytest.mark.parametrize(
    "observation",
    [
        ConfigObservation(_UserPayload("wrong")),
        ToolObservation(_UserPayload("wrong")),
        UserObservation(_ToolPayload("wrong")),
        AssistantObservation(_ConfigPayload("wrong")),
        ConfigObservation(cast(Never, {"mutable": True})),
    ],
)
def test_admission_rejects_wrong_or_mutable_payload_classes(observation: object) -> None:
    with pytest.raises(ObserveContractError, match="unexpected concrete type"):
        _admission().admit_observation(cast(Never, observation))


def test_admission_rejects_unknown_observations_and_forged_delivery_revisions() -> None:
    with pytest.raises(ObserveContractError, match="exact DeliveryAck"):
        _admission().admit_ack(cast(Never, object()))
    with pytest.raises(ObserveContractError, match="one of Config"):
        _admission().admit_observation(cast(Never, object()))
    forged: ObservationDelivery[object] = cast(
        ObservationDelivery[object],
        object.__new__(ObservationDelivery),
    )
    object.__setattr__(forged, "delivery_id", DeliveryId("delivery"))
    object.__setattr__(forged, "cursor_before", _cursor(0))
    object.__setattr__(forged, "cursor_after", _cursor(1))
    object.__setattr__(forged, "payload", UserObservation("user"))
    object.__setattr__(forged, "observation_revision", True)
    with pytest.raises(ObserveContractError, match="revision"):
        _admission().admit_delivery(cast(Never, forged))


def test_admission_enforces_the_complete_batch_delivery_limit() -> None:
    deliveries = tuple(_delivery(index, UserObservation(index), f"delivery-{index}") for index in range(4_097))
    forged = object.__new__(ObservationBatch)
    object.__setattr__(forged, "deliveries", deliveries)
    with pytest.raises(ObserveContractError, match=r"delivery limit|contiguous"):
        _admission().admit_batch(cast(Never, forged))


def test_admission_rejects_a_config_batch_at_the_context_boundary() -> None:
    config = ConfigBatch((_delivery(0, ConfigObservation("config"), "config"),))
    with pytest.raises(ObserveContractError, match="exact ToolBatch"):
        _admission().admit_context_batch(cast(Never, config))


def test_admission_batch_subset_checks_shape_limit_and_family_selector() -> None:
    admission = _admission()
    with pytest.raises(ObserveContractError, match="non-empty tuple"):
        admission.admit_batch_subset(cast(Never, []), UserObservation, "user batch")
    with pytest.raises(ObserveContractError, match="unknown observation family"):
        admission.admit_batch_subset(
            (_delivery(0, UserObservation("user"), "user"),),
            cast(Never, object()),
            "unknown batch",
        )
    deliveries = tuple(_delivery(index, UserObservation(index), f"user-{index}") for index in range(4_097))
    with pytest.raises(ObserveContractError, match="delivery limit"):
        admission.admit_batch_subset(deliveries, UserObservation, "user batch")


def test_admission_delivery_id_collections_are_nonempty_bounded_and_distinct() -> None:
    admission = _admission()
    with pytest.raises(ObserveContractError, match="non-empty tuple"):
        admission.admit_delivery_ids(cast(Never, ()), "ids")
    with pytest.raises(ObserveContractError, match="distinct"):
        admission.admit_delivery_ids((DeliveryId("same"), DeliveryId("same")), "ids")
    ids = tuple(DeliveryId(f"delivery-{index}") for index in range(4_097))
    with pytest.raises(ObserveContractError, match="delivery limit"):
        admission.admit_delivery_ids(ids, "ids")


def test_admission_revalidates_forged_conflict_and_read_variants() -> None:
    deliveries = (
        _delivery(0, UserObservation("user"), "user"),
        _delivery(1, ToolObservation("tool"), "tool"),
    )
    conflict = ObservationConflict(
        deliveries,
        (NonConfigObservationFamily.USER, NonConfigObservationFamily.TOOL),
    )
    forged = object.__new__(ObservationConflict)
    object.__setattr__(forged, "deliveries", conflict.deliveries)
    object.__setattr__(forged, "families", (NonConfigObservationFamily.USER,))
    with pytest.raises(ObserveContractError, match=r"at least two|match"):
        _admission().admit_conflict(cast(Never, forged))
    with pytest.raises(ObserveContractError, match="queue read"):
        _admission().admit_read(cast(Never, object()))
    with pytest.raises(ObserveContractError, match="does not start"):
        _admission().admit_read_after(_empty(1), _cursor(0))
    with pytest.raises(ObserveContractError, match="conflicting"):
        conflicted = object.__new__(Available)
        object.__setattr__(conflicted, "batch", ObservationBatch(deliveries))
        object.__setattr__(conflicted, "boundary", _boundary(0, 2))
        _admission().admit_read(cast(Never, conflicted))


def test_admission_rejects_a_forged_snapshot_with_non_tuple_tasks() -> None:
    forged = object.__new__(BackgroundTaskSnapshot)
    object.__setattr__(forged, "observation_revision", 1)
    object.__setattr__(forged, "blocking_tasks", [])
    with pytest.raises(ObserveContractError, match="blocking_tasks"):
        _admission().admit_task_snapshot(cast(Never, forged))


def test_admission_applies_the_delivery_limit_to_conflicts() -> None:
    deliveries = tuple(
        _delivery(
            index,
            UserObservation(str(index)) if index % 2 == 0 else ToolObservation(str(index)),
            f"delivery-{index}",
        )
        for index in range(ObservePayloadAdmission.DELIVERY_MAX_COUNT + 1)
    )
    conflict = ObservationConflict(
        deliveries,
        (NonConfigObservationFamily.USER, NonConfigObservationFamily.TOOL),
    )

    with pytest.raises(ObserveContractError, match="delivery limit"):
        _admission().admit_conflict(conflict)


def test_admission_revalidates_optional_receipt_snapshots() -> None:
    boundary = _boundary()
    forged = object.__new__(ContextAppendReceipt)
    object.__setattr__(forged, "delivery_ids", (DeliveryId("user"),))
    object.__setattr__(forged, "read_boundary", boundary)
    object.__setattr__(forged, "settlement_boundary", boundary)
    object.__setattr__(forged, "settlement_id", "context")
    object.__setattr__(forged, "family", NonConfigObservationFamily.USER)
    object.__setattr__(forged, "background_task_snapshot", cast(Never, object()))
    with pytest.raises(ObserveContractError, match="snapshot"):
        _admission().admit_context_receipt(cast(Never, forged))

    successor = ObservationBoundary("stream", _cursor(1), _cursor(1), 2)
    receipt = ContextAppendReceipt(
        (DeliveryId("user"),),
        boundary,
        successor,
        "context-successor",
        NonConfigObservationFamily.USER,
        BackgroundTaskSnapshot(2, ()),
    )
    assert _admission().admit_context_receipt(receipt) is receipt


def test_admission_rejects_an_unknown_stage_value_and_invalid_envelope_stage() -> None:
    with pytest.raises(ObserveContractError, match="known concrete stage"):
        _admission().admit_stage_value(cast(Never, object()))
    forged = object.__new__(ObserveHookEnvelope)
    object.__setattr__(forged, "stage", cast(Never, object()))
    object.__setattr__(forged, "payload", GetObservationStageValue(_user_frame()))
    object.__setattr__(forged, "hook_state", _State())
    with pytest.raises(ObserveContractError, match=r"malformed|stage"):
        _admission().admit_hook_envelope(cast(Never, forged))


def test_admission_checks_hook_request_state_and_stage_identity() -> None:
    frame = _user_frame()
    envelope = ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, GetObservationStageValue(frame), _State())
    with pytest.raises(ObserveContractError, match=r"unexpected|does not match"):
        _admission().admit_hook_request(HookActivationRequest(envelope, _OtherState(), GraphNodeId("get_observation")))
    with pytest.raises(ObserveContractError, match="concrete HookStateProjection"):
        _admission().admit_hook_request(
            HookActivationRequest(envelope, HookStateProjection(), GraphNodeId("get_observation"))
        )
    with pytest.raises(ObserveContractError, match="does not match its envelope"):
        _admission().admit_hook_request(
            HookActivationRequest(envelope, _State("changed"), GraphNodeId("get_observation"))
        )
    with pytest.raises(ObserveContractError, match="node_id"):
        _admission().admit_hook_request(HookActivationRequest(envelope, _State(), GraphNodeId("write_observation")))

    with pytest.raises(ObserveContractError, match="stage is unknown"):
        unknown = ObserveHookEnvelope(_unknown_hook_stage(), GetObservationStageValue(frame), _State())
        _admission().admit_hook_request(HookActivationRequest(unknown, _State(), GraphNodeId("get_observation")))


def test_admission_rejects_abstract_or_mixed_hook_commands() -> None:
    frame = _user_frame()
    envelope = ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, GetObservationStageValue(frame), _State())
    with pytest.raises(ObserveContractError, match="concrete ObserveHookCommand"):
        _admission().admit_hook_result(HookResult(envelope, (ObserveHookCommand(),), GraphNodeId("get_observation")))
    with pytest.raises(ObserveContractError, match="unexpected concrete command type"):
        _admission().admit_hook_result(
            HookResult(envelope, (_Command("one"), _OtherCommand()), GraphNodeId("get_observation"))
        )


def test_transition_admission_allows_a_payload_rewrite_within_the_same_business_stage() -> None:
    admission = _admission()
    state = _State()
    original = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(_user_frame()),
        state,
    )
    config = _available(_delivery(0, ConfigObservation("rewritten"), "rewritten"))
    rewritten_frame = ObserveFrame(config.batch, config.boundary, _snapshot())
    rewritten = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(rewritten_frame),
        state,
    )

    admission.admit_transition(
        HookActivationRequest(original, state, GraphNodeId("get_observation")),
        HookStageResult(rewritten, (_Command("rewrite"),)),
    )


def test_transition_admission_preserves_stage_state_and_exact_commands() -> None:
    admission = _admission()
    state = _State()
    envelope = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(_user_frame()),
        state,
    )
    request = HookActivationRequest(envelope, state, GraphNodeId("get_observation"))

    with pytest.raises(ObserveContractError, match="HookStageResult"):
        admission.admit_transition(
            request,
            cast(HookStageResult[ObserveHookEnvelope, _Command], object()),
        )

    changed_stage = ObserveHookEnvelope(
        ObserveHookStage.AFTER_WRITE_OBSERVATION,
        WriteObservationStageValue(_user_result()),
        state,
    )
    with pytest.raises(ObserveContractError, match="business stage"):
        admission.admit_transition(request, HookStageResult(changed_stage))

    changed_state = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(_user_frame()),
        _State("changed"),
    )
    with pytest.raises(ObserveContractError, match="read-only state"):
        admission.admit_transition(request, HookStageResult(changed_state))

    forged = cast(
        HookStageResult[ObserveHookEnvelope, _Command],
        object.__new__(HookStageResult),
    )
    object.__setattr__(forged, "value", envelope)
    object.__setattr__(forged, "commands", [])
    with pytest.raises(ObserveContractError, match="commands must be a tuple"):
        admission.admit_transition(request, forged)

    with pytest.raises(ObserveContractError, match="unexpected concrete command type"):
        admission.admit_transition(
            request,
            cast(
                HookStageResult[ObserveHookEnvelope, _Command],
                HookStageResult(envelope, (_OtherCommand(),)),
            ),
        )


def test_admission_accepts_a_valid_result_and_ack() -> None:
    result = _user_result()
    admission = _admission()
    assert admission.admit_result(result) is result
    ack_reference = result.observation_receipt.ack_reference
    ack = DeliveryAck(ack_reference)
    assert admission.admit_ack(ack) is ack
    assert admission.admit_ack_reference(ack_reference) is ack_reference

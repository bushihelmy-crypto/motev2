"""Contract and fail-closed admission tests for Observe values."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from typing import Never, cast

import pytest

from mote_kernel.hooks.contract import HookActivationRequest, HookResult
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.observe.admission import ObservePayloadAdmission
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
    NonConfigObservationFamily,
    Observation,
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
    ObserveRequest,
    ObserveResult,
    ObserveStageValue,
    ToolBatch,
    ToolObservation,
    UserBatch,
    UserObservation,
    WriteObservationStageValue,
    observation_kind,
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
    ObserveIdentityError,
    WaitRegistration,
)
from mote_kernel.state.graph_state import GraphDefinitionId, GraphNodeId


@dataclass(frozen=True, slots=True)
class _State(HookStateProjection):
    marker: str = "state"


@dataclass(frozen=True, slots=True)
class _Command(ObserveHookCommand):
    marker: str = "command"


class _ConfigPayload(str, ObservationPayload):
    """Immutable nominal Config payload used by this contract suite."""


class _ToolPayload(str, ObservationPayload):
    """Immutable nominal Tool payload used by this contract suite."""


class _UserPayload(str, ObservationPayload):
    """Immutable nominal User payload used by this contract suite."""


class _AssistantPayload(str, ObservationPayload):
    """Immutable nominal Assistant payload used by this contract suite."""


def _admission() -> ObservePayloadAdmission:
    return ObservePayloadAdmission(
        _State,
        _Command,
        _ConfigPayload,
        _ToolPayload,
        _UserPayload,
        _AssistantPayload,
    )


def _cursor(sequence: int = 0) -> ObservationCursor:
    return ObservationCursor("stream", sequence)


def _delivery(
    sequence: int,
    observation: (ConfigObservation[str] | ToolObservation[str] | UserObservation[str] | AssistantObservation[str]),
    delivery_id: str,
) -> ObservationDelivery[Observation]:
    normalized: Observation
    if type(observation) is ConfigObservation:
        normalized = ConfigObservation(_ConfigPayload(str(observation.payload)))
    elif type(observation) is ToolObservation:
        normalized = ToolObservation(_ToolPayload(str(observation.payload)))
    elif type(observation) is UserObservation:
        normalized = UserObservation(_UserPayload(str(observation.payload)))
    else:
        normalized = AssistantObservation(_AssistantPayload(str(observation.payload)))
    return ObservationDelivery(
        DeliveryId(delivery_id),
        _cursor(sequence),
        _cursor(sequence + 1),
        normalized,
        1,
    )


def _boundary(before: int = 0, after: int = 1, revision: int = 1) -> ObservationBoundary:
    return ObservationBoundary("stream", _cursor(before), _cursor(after), revision)


def _snapshot(revision: int = 1, *tasks: BlockingTaskRef) -> BackgroundTaskSnapshot:
    return BackgroundTaskSnapshot(revision, tasks)


def _receipt_for(
    delivery_ids: tuple[DeliveryId, ...],
    *,
    config: bool,
    boundary: ObservationBoundary | None = None,
) -> ObservationBatchReceipt:
    selected = _boundary(0, len(delivery_ids)) if boundary is None else boundary
    child_config = ConfigSettlementReceipt(delivery_ids, selected, selected, "config-settlement") if config else None
    child_context = (
        None
        if config
        else ContextAppendReceipt(
            delivery_ids,
            selected,
            selected,
            "context-settlement",
            NonConfigObservationFamily.USER,
        )
    )
    return ObservationBatchReceipt(
        selected,
        selected,
        child_config,
        child_context,
        DeliveryAckReference("stream", delivery_ids, "ack"),
    )


def test_wait_codec_round_trips_non_ascii_identity_without_runtime_state() -> None:
    wait = ObservationWait("消息流", ObservationCursor("消息流", 7), 3, "新消息")

    assert ObservationWait.decode(wait.encode()) == wait


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"1:x",
        b"18:mote.observe.wait.v1",
        b"1:x1:s1:01:01:w",
        b"1:x1:s1:11:1:1:wtrailing",
        b"1:x1:s1:11:1:1:\xff",
    ],
)
def test_wait_codec_rejects_malformed_payloads(payload: bytes) -> None:
    with pytest.raises(ObserveIdentityError):
        ObservationWait.decode(payload)


def test_wait_registration_must_match_the_wait_coordinate() -> None:
    wait = ObservationWait("stream", _cursor(4), 9, "delivery")
    registration = WaitRegistration("wait", "stream", _cursor(5), 9)

    with pytest.raises(ObserveContractError, match="does not match"):
        _admission().admit_wait_registration_for(wait, registration)


def test_admission_applies_the_delivery_limit_to_ack_references() -> None:
    reference = DeliveryAckReference(
        "stream",
        tuple(DeliveryId(f"delivery-{index}") for index in range(ObservePayloadAdmission.DELIVERY_MAX_COUNT + 1)),
        "settlement",
    )

    with pytest.raises(ObserveContractError, match="delivery limit"):
        _admission().admit_ack_reference(reference)


def test_admission_rejects_a_forged_observe_hook_slot() -> None:
    forged = object.__new__(HookSlotId)
    object.__setattr__(forged, "definition_id", GraphDefinitionId("observe"))
    object.__setattr__(forged, "definition_version", True)
    object.__setattr__(forged, "node_id", GraphNodeId("hook"))
    object.__setattr__(forged, "stage", HookStage.AFTER_NODE)

    with pytest.raises(ObserveContractError, match="Observe Hook slot"):
        _admission().admit_observe_slot(forged)


def test_observation_batch_rejects_a_cursor_gap_and_duplicate_delivery_id() -> None:
    first = _delivery(0, ToolObservation("first"), "same")
    gap = ObservationDelivery(DeliveryId("second"), _cursor(2), _cursor(3), ToolObservation("gap"), 1)
    duplicate = _delivery(1, ToolObservation("second"), "same")

    with pytest.raises(ObserveContractError, match="contiguous"):
        ObservationBatch((first, gap))
    with pytest.raises(ObserveContractError, match="distinct delivery ids"):
        ObservationBatch((first, duplicate))


def test_only_one_non_config_family_is_allowed_in_an_available_read() -> None:
    tool = _delivery(0, ToolObservation("tool"), "tool")
    user = _delivery(1, UserObservation("user"), "user")
    batch = ObservationBatch((tool, user))

    with pytest.raises(ObserveContractError, match="conflicting"):
        Available(batch, _boundary(0, 2))


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: Available(cast(ObservationBatch, object()), _boundary(0, 0)), "available batch"),
        (lambda: Conflict(cast(ObservationConflict, object()), _boundary(0, 0)), "conflict value"),
        (lambda: observation_kind(cast(ObservationBatch, object())), "observation_kind"),
    ],
)
def test_observation_outer_boundaries_reject_wrong_nested_types_without_attribute_errors(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ObserveContractError, match=message):
        factory()


def test_conflict_families_are_derived_in_first_seen_fifo_order() -> None:
    user = _delivery(0, UserObservation("user"), "user")
    tool = _delivery(1, ToolObservation("tool"), "tool")
    batch = ObservationBatch((user, tool))
    conflict = ObservationConflict(batch.deliveries, batch.non_config_families)

    assert tuple(family.value for family in conflict.families) == ("user", "tool")


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (ConfigObservation("config"), ObservationKind.CONFIG),
        (ToolObservation("tool"), ObservationKind.TOOL),
        (UserObservation("user"), ObservationKind.USER),
        (AssistantObservation("assistant"), ObservationKind.ASSISTANT),
    ],
)
def test_observation_kind_is_a_closed_four_value_projection(
    observation: (ConfigObservation[str] | ToolObservation[str] | UserObservation[str] | AssistantObservation[str]),
    expected: ObservationKind,
) -> None:
    assert observation_kind(ObservationBatch((_delivery(0, observation, "delivery"),))) is expected


def test_admission_rejects_a_forged_conflicting_frame_before_any_port_can_use_it() -> None:
    tool = _delivery(0, ToolObservation("tool"), "tool")
    user = _delivery(1, UserObservation("user"), "user")
    forged = object.__new__(ObserveFrame)
    object.__setattr__(forged, "batch", ObservationBatch((tool, user)))
    object.__setattr__(forged, "boundary", _boundary(0, 2))
    object.__setattr__(forged, "background_task_snapshot", BackgroundTaskSnapshot(1, ()))

    with pytest.raises(ObserveContractError, match="conflicting"):
        _admission().admit_frame(forged)


def test_admission_revalidates_a_forged_family_batch_fifo_shape() -> None:
    first = _delivery(0, ConfigObservation("first"), "first")
    overlap = ObservationDelivery(DeliveryId("overlap"), _cursor(0), _cursor(2), ConfigObservation("overlap"), 1)
    forged = object.__new__(ConfigBatch)
    object.__setattr__(forged, "deliveries", (first, overlap))

    with pytest.raises(ObserveContractError, match=r"contiguous|FIFO"):
        _admission().admit_config_batch(forged)


@pytest.mark.parametrize("batch_type", [ToolBatch, UserBatch, AssistantBatch])
def test_admission_rejects_a_forged_context_batch_without_leaking_attribute_errors(
    batch_type: type[ToolBatch] | type[UserBatch] | type[AssistantBatch],
) -> None:
    forged = object.__new__(batch_type)

    with pytest.raises(ObserveContractError, match="batch"):
        _admission().admit_context_batch(cast(ContextObservationBatch, forged))


def test_observation_batch_keeps_deliveries_as_its_only_stored_representation() -> None:
    delivery = _delivery(0, UserObservation("user"), "user")
    forged = object.__new__(ObservationBatch)
    object.__setattr__(forged, "deliveries", (delivery,))

    admitted = _admission().admit_batch(forged)

    assert admitted.user == UserBatch((delivery,))
    assert not hasattr(admitted, "_config")
    assert not hasattr(admitted, "_tool")
    assert not hasattr(admitted, "_user")
    assert not hasattr(admitted, "_assistant")


def test_hook_admission_keeps_the_predecessor_identity_outside_script_output() -> None:
    delivery = _delivery(0, UserObservation("user"), "user")
    batch = ObservationBatch((delivery,))
    frame = ObserveFrame(batch, _boundary(0, 1), BackgroundTaskSnapshot(1, ()))
    envelope = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(frame),
        _State(),
    )
    admission = _admission()

    request = cast(
        HookActivationRequest[ObserveHookEnvelope, HookStateProjection],
        HookActivationRequest(envelope, _State(), GraphNodeId("write_observation")),
    )
    with pytest.raises(ObserveContractError, match="node_id"):
        admission.admit_hook_request(request)

    result = cast(
        HookResult[ObserveHookEnvelope, ObserveHookCommand],
        HookResult(envelope, (_Command(),), GraphNodeId("write_observation")),
    )
    with pytest.raises(ObserveContractError, match="node_id"):
        admission.admit_hook_result(result)


def test_result_receipt_must_cover_exactly_the_settled_delivery_ids() -> None:
    delivery = _delivery(0, ConfigObservation("config"), "config")
    read_boundary = _boundary(0, 1)
    config_receipt = ConfigSettlementReceipt(
        (delivery.delivery_id,),
        read_boundary,
        read_boundary,
        "config-settlement",
    )
    bad_ack = DeliveryAckReference("stream", (DeliveryId("other"),), "ack")

    with pytest.raises(ObserveContractError, match="does not match settled deliveries"):
        ObservationBatchReceipt(read_boundary, read_boundary, config_receipt, None, bad_ack)


def test_result_receipt_rejects_reversed_delivery_ids_within_a_family() -> None:
    first = _delivery(0, ConfigObservation("first"), "first")
    second = _delivery(1, ConfigObservation("second"), "second")
    boundary = _boundary(0, 2)
    config_receipt = ConfigSettlementReceipt(
        (second.delivery_id, first.delivery_id),
        boundary,
        boundary,
        "config-settlement",
    )

    with pytest.raises(ObserveContractError, match="family FIFO"):
        ObservationBatchReceipt(
            boundary,
            boundary,
            config_receipt,
            None,
            DeliveryAckReference("stream", (first.delivery_id, second.delivery_id), "ack"),
        )


def test_revision_advanced_settlement_requires_a_matching_successor_snapshot() -> None:
    delivery = _delivery(0, ConfigObservation("config"), "config")
    read_boundary = _boundary(0, 1, revision=1)
    successor = _boundary(1, 1, revision=2)
    with pytest.raises(ObserveContractError, match="requires a background task snapshot"):
        ConfigSettlementReceipt(
            (delivery.delivery_id,),
            read_boundary,
            successor,
            "config-settlement",
        )

    with pytest.raises(ObserveContractError, match="requires a background task snapshot"):
        ContextAppendReceipt(
            (delivery.delivery_id,),
            read_boundary,
            successor,
            "context-settlement",
            NonConfigObservationFamily.USER,
        )


def test_settlement_at_the_same_revision_must_preserve_the_read_boundary() -> None:
    delivery = _delivery(0, ConfigObservation("config"), "config")
    read_boundary = _boundary(0, 1, revision=1)
    advanced_cursor = _boundary(1, 2, revision=1)

    with pytest.raises(ObserveContractError, match="revision is unchanged"):
        ConfigSettlementReceipt(
            (delivery.delivery_id,),
            read_boundary,
            advanced_cursor,
            "config-settlement",
        )


def test_revision_successor_cannot_skip_a_delivery_after_the_read_batch() -> None:
    delivery = _delivery(0, ConfigObservation("config"), "config")
    read_boundary = _boundary(0, 1, revision=1)
    skipped_cursor = _boundary(1, 2, revision=2)

    with pytest.raises(ObserveContractError, match="successor boundary"):
        ConfigSettlementReceipt(
            (delivery.delivery_id,),
            read_boundary,
            skipped_cursor,
            "config-settlement",
        )


def test_revision_successor_is_a_zero_length_boundary_at_the_batch_end() -> None:
    delivery = _delivery(0, ConfigObservation("config"), "config")
    read_boundary = _boundary(0, 1, revision=1)
    successor = _boundary(1, 1, revision=2)
    snapshot = BackgroundTaskSnapshot(2, ())
    config_receipt = ConfigSettlementReceipt(
        (delivery.delivery_id,),
        read_boundary,
        successor,
        "config-settlement",
        snapshot,
    )
    batch_receipt = ObservationBatchReceipt(
        read_boundary,
        successor,
        config_receipt,
        None,
        DeliveryAckReference("stream", (delivery.delivery_id,), "ack"),
    )

    result = ObserveResult(
        ObservationKind.CONFIG,
        (delivery.delivery_id,),
        successor.cursor_range,
        snapshot,
        batch_receipt,
    )

    assert result.cursor_range == successor.cursor_range


def test_result_snapshot_must_match_the_complete_settlement_evidence() -> None:
    delivery = _delivery(0, ConfigObservation("config"), "config")
    read_boundary = _boundary(0, 1, revision=1)
    successor = _boundary(1, 1, revision=2)
    receipt_snapshot = BackgroundTaskSnapshot(2, (BlockingTaskRef("settled", 1, "wait"),))
    result_snapshot = BackgroundTaskSnapshot(2, (BlockingTaskRef("replaced", 1, "wait"),))
    config_receipt = ConfigSettlementReceipt(
        (delivery.delivery_id,),
        read_boundary,
        successor,
        "config-settlement",
        receipt_snapshot,
    )
    batch_receipt = ObservationBatchReceipt(
        read_boundary,
        successor,
        config_receipt,
        None,
        DeliveryAckReference("stream", (delivery.delivery_id,), "ack"),
    )

    with pytest.raises(ObserveContractError, match="task snapshot does not match settlement evidence"):
        ObserveResult(
            ObservationKind.CONFIG,
            (delivery.delivery_id,),
            successor.cursor_range,
            result_snapshot,
            batch_receipt,
        )


def test_context_receipt_preserves_context_payload_concrete_family() -> None:
    delivery = _delivery(0, UserObservation("user"), "user")
    boundary = _boundary(0, 1)
    context_receipt = ContextAppendReceipt(
        (delivery.delivery_id,),
        boundary,
        boundary,
        "context",
        NonConfigObservationFamily.USER,
    )
    receipt = ObservationBatchReceipt(
        boundary,
        boundary,
        None,
        context_receipt,
        DeliveryAckReference("stream", (delivery.delivery_id,), "ack"),
    )

    assert receipt.delivery_ids == (DeliveryId("user"),)
    assert receipt.current_state is ObservationKind.USER


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: ConfigObservation(cast(Never, None)), "config observation payload"),
        (lambda: ToolObservation(cast(Never, None)), "tool observation payload"),
        (lambda: UserObservation(cast(Never, None)), "user observation payload"),
        (lambda: AssistantObservation(cast(Never, None)), "assistant observation payload"),
    ],
)
def test_observation_payloads_are_required_but_falsy_values_are_valid(
    factory: Callable[[], object], message: str
) -> None:
    with pytest.raises(ObserveContractError, match=message):
        factory()
    assert ConfigObservation("").payload == ""
    assert ToolObservation(False).payload is False
    assert UserObservation(0).payload == 0
    assert AssistantObservation(()).payload == ()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: ObservationDelivery(cast(Never, object()), _cursor(0), _cursor(1), UserObservation("u"), 1),
            "delivery_id",
        ),
        (
            lambda: ObservationDelivery(DeliveryId("d"), cast(Never, object()), _cursor(1), UserObservation("u"), 1),
            "cursor_before",
        ),
        (
            lambda: ObservationDelivery(DeliveryId("d"), _cursor(0), cast(Never, object()), UserObservation("u"), 1),
            "cursor_after",
        ),
        (lambda: ObservationDelivery(DeliveryId("d"), _cursor(0), _cursor(1), cast(Never, object()), 1), "payload"),
        (
            lambda: ObservationDelivery(
                DeliveryId("d"), _cursor(0), ObservationCursor("other", 1), UserObservation("u"), 1
            ),
            "one stream",
        ),
        (lambda: ObservationDelivery(DeliveryId("d"), _cursor(1), _cursor(1), UserObservation("u"), 1), "advance"),
        (
            lambda: ObservationDelivery(
                DeliveryId("d"), _cursor(0), _cursor(1), UserObservation("u"), cast(Never, True)
            ),
            "revision",
        ),
    ],
)
def test_delivery_rejects_invalid_coordinates_and_payloads(factory: Callable[[], object], message: str) -> None:
    with pytest.raises(ObserveContractError, match=message):
        factory()


def test_delivery_properties_preserve_stream_and_cursor_range() -> None:
    delivery = _delivery(2, UserObservation("user"), "delivery")
    assert delivery.stream_id == "stream"
    assert delivery.cursor_range == CursorRange(_cursor(2), _cursor(3))


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: ObservationBatch(cast(Never, ())), "non-empty tuple"),
        (lambda: ObservationBatch(cast(Never, [])), "non-empty tuple"),
        (lambda: ObservationBatch((cast(Never, object()),)), "ObservationDelivery"),
        (
            lambda: ObservationBatch(
                (_delivery(0, UserObservation("u"), "u"), _delivery(1, UserObservation("u2"), "u"))
            ),
            "distinct",
        ),
        (
            lambda: ObservationBatch(
                (_delivery(0, UserObservation("u"), "u"), _delivery(2, UserObservation("u2"), "u2"))
            ),
            "contiguous",
        ),
        (
            lambda: ObservationBatch(
                (
                    _delivery(0, UserObservation("u"), "u"),
                    ObservationDelivery(DeliveryId("u2"), _cursor(1), _cursor(2), UserObservation("u2"), 2),
                )
            ),
            "revision",
        ),
    ],
)
def test_observation_batch_enforces_complete_fifo_windows(factory: Callable[[], object], message: str) -> None:
    with pytest.raises(ObserveContractError, match=message):
        factory()


def test_complete_batch_rejects_a_delivery_from_another_stream() -> None:
    first = _delivery(0, UserObservation("first"), "first")
    second = ObservationDelivery(
        DeliveryId("second"),
        ObservationCursor("other", 1),
        ObservationCursor("other", 2),
        UserObservation(_UserPayload("second")),
        1,
    )

    with pytest.raises(ObserveContractError, match="one stream"):
        ObservationBatch((first, second))


def test_observation_batch_derives_all_family_projections_without_truncating_fifo() -> None:
    deliveries = (
        _delivery(0, ConfigObservation("c1"), "c1"),
        _delivery(1, UserObservation("u1"), "u1"),
        _delivery(2, ConfigObservation("c2"), "c2"),
        _delivery(3, UserObservation("u2"), "u2"),
    )
    batch = ObservationBatch(deliveries)
    assert batch.delivery_ids == tuple(DeliveryId(value) for value in ("c1", "u1", "c2", "u2"))
    assert batch.config is not None and batch.config.effective == ConfigObservation("c2")
    assert batch.user is not None and batch.user.delivery_ids == (DeliveryId("u1"), DeliveryId("u2"))
    assert batch.tool is None
    assert batch.assistant is None
    assert batch.stream_id == "stream"
    assert batch.observation_revision == 1


@pytest.mark.parametrize("batch_type", [ConfigBatch, ToolBatch, UserBatch, AssistantBatch])
def test_family_batches_reject_empty_or_wrong_family_payloads(batch_type: type[object]) -> None:
    with pytest.raises(ObserveContractError, match="non-empty tuple"):
        cast(Callable[[object], object], batch_type)(cast(Never, ()))
    with pytest.raises(ObserveContractError, match="ObservationDelivery"):
        cast(Callable[[object], object], batch_type)((cast(Never, object()),))
    wrong = _delivery(0, UserObservation("user"), "user")
    if batch_type is UserBatch:
        return
    with pytest.raises(ObserveContractError, match="different observation family"):
        cast(Callable[[object], object], batch_type)((wrong,))


def test_family_batch_preserves_fifo_order_across_interleaved_projection_gaps() -> None:
    first = _delivery(0, UserObservation("one"), "one")
    second = _delivery(2, UserObservation("two"), "two")
    batch = UserBatch((first, second))
    assert batch.delivery_ids == (DeliveryId("one"), DeliveryId("two"))


def test_family_batch_rejects_backtracking_stream_or_revision() -> None:
    first = _delivery(0, UserObservation("one"), "one")
    overlap = ObservationDelivery(DeliveryId("two"), _cursor(0), _cursor(1), UserObservation("two"), 1)
    other_stream = ObservationDelivery(
        DeliveryId("three"), ObservationCursor("other", 1), ObservationCursor("other", 2), UserObservation("three"), 1
    )
    other_revision = ObservationDelivery(DeliveryId("four"), _cursor(2), _cursor(3), UserObservation("four"), 2)
    with pytest.raises(ObserveContractError, match="FIFO"):
        UserBatch((first, overlap))
    with pytest.raises(ObserveContractError, match="one stream"):
        UserBatch((first, other_stream))
    with pytest.raises(ObserveContractError, match="one observation revision"):
        UserBatch((first, other_revision))


def test_conflict_requires_distinct_first_seen_non_config_families() -> None:
    deliveries = (
        _delivery(0, UserObservation("u"), "u"),
        _delivery(1, ToolObservation("t"), "t"),
        _delivery(2, AssistantObservation("a"), "a"),
    )
    batch = ObservationBatch(deliveries)
    conflict = ObservationConflict(batch.deliveries, batch.non_config_families)
    assert conflict.families_for_deliveries == (
        NonConfigObservationFamily.USER,
        NonConfigObservationFamily.TOOL,
        NonConfigObservationFamily.ASSISTANT,
    )
    assert conflict.delivery_ids == batch.delivery_ids
    with pytest.raises(ObserveContractError, match="at least two"):
        ObservationConflict(deliveries, (NonConfigObservationFamily.USER,))
    with pytest.raises(ObserveContractError, match="distinct"):
        ObservationConflict(deliveries, (NonConfigObservationFamily.USER, NonConfigObservationFamily.USER))
    with pytest.raises(ObserveContractError, match="match"):
        ObservationConflict(deliveries, (NonConfigObservationFamily.TOOL, NonConfigObservationFamily.USER))
    with pytest.raises(ObserveContractError, match="NonConfigObservationFamily"):
        ObservationConflict(
            deliveries,
            cast(
                tuple[NonConfigObservationFamily, ...],
                (NonConfigObservationFamily.USER, "tool", NonConfigObservationFamily.ASSISTANT),
            ),
        )


def test_read_boundaries_reject_wrong_nested_types_and_mismatched_coordinates() -> None:
    delivery = _delivery(0, UserObservation("user"), "user")
    batch = ObservationBatch((delivery,))
    with pytest.raises(ObserveContractError, match="boundary"):
        Available(batch, cast(Never, object()))
    with pytest.raises(ObserveContractError, match="does not match"):
        Available(batch, _boundary(1, 2))
    wait = ObservationWait("stream", _cursor(0), 1, "delivery")
    with pytest.raises(ObserveContractError, match="wait"):
        Empty(cast(Never, object()), _boundary(0, 0))
    with pytest.raises(ObserveContractError, match="boundary"):
        Empty(wait, cast(Never, object()))
    with pytest.raises(ObserveContractError, match="one stream"):
        Empty(ObservationWait("other", ObservationCursor("other", 0), 1, "delivery"), _boundary(0, 0))
    with pytest.raises(ObserveContractError, match="one revision"):
        Empty(wait, _boundary(0, 0, revision=2))
    with pytest.raises(ObserveContractError, match="must begin after"):
        Empty(ObservationWait("stream", _cursor(1), 1, "delivery"), _boundary(0, 0))
    with pytest.raises(ObserveContractError, match="conflict value"):
        Conflict(cast(Never, object()), _boundary(0, 0))


def test_background_task_snapshot_is_immutable_and_deduplicated_by_incarnation() -> None:
    task = BlockingTaskRef("task", 1, "wait")
    assert _snapshot(1, task).has_blocking_tasks
    assert not _snapshot(1).has_blocking_tasks
    with pytest.raises(ObserveContractError, match="invalid task"):
        BackgroundTaskSnapshot(1, (cast(Never, object()),))
    with pytest.raises(ObserveContractError, match="repeat"):
        BackgroundTaskSnapshot(1, (task, task))
    with pytest.raises(ObserveContractError, match="revision"):
        BackgroundTaskSnapshot(cast(Never, True), ())


def test_background_task_snapshot_has_one_canonical_task_order() -> None:
    later = BlockingTaskRef("z-task", 1, "wait")
    earlier_incarnation = BlockingTaskRef("a-task", 1, "wait")
    later_incarnation = BlockingTaskRef("a-task", 2, "wait")

    snapshot = BackgroundTaskSnapshot(1, (later, later_incarnation, earlier_incarnation))

    assert snapshot.blocking_tasks == (earlier_incarnation, later_incarnation, later)
    assert snapshot == BackgroundTaskSnapshot(1, (later_incarnation, later, earlier_incarnation))


def test_admission_rejects_a_forged_noncanonical_task_order() -> None:
    later = BlockingTaskRef("z-task", 1, "wait")
    earlier = BlockingTaskRef("a-task", 1, "wait")
    forged = object.__new__(BackgroundTaskSnapshot)
    object.__setattr__(forged, "observation_revision", 1)
    object.__setattr__(forged, "blocking_tasks", (later, earlier))

    with pytest.raises(ObserveContractError, match="not canonical"):
        _admission().admit_task_snapshot(forged)


def test_settlement_boundary_validation_accepts_only_same_boundary_or_zero_length_successor() -> None:
    read = _boundary(0, 1, 1)
    assert _receipt_for((DeliveryId("d"),), config=True).settlement_boundary == _boundary(0, 1, 1)
    successor = _boundary(1, 1, 2)
    ConfigSettlementReceipt((DeliveryId("d"),), read, successor, "settlement", _snapshot(2))
    with pytest.raises(ObserveContractError, match="zero-length"):
        ConfigSettlementReceipt((DeliveryId("d"),), read, _boundary(1, 2, 2), "settlement")
    with pytest.raises(ObserveContractError, match="cannot move backwards"):
        ConfigSettlementReceipt((DeliveryId("d"),), read, _boundary(1, 1, 0), "settlement")
    other_cursor = ObservationCursor("other", 1)
    other_stream = ObservationBoundary("other", other_cursor, other_cursor, 2)
    with pytest.raises(ObserveContractError, match="one stream"):
        ConfigSettlementReceipt((DeliveryId("d"),), read, other_stream, "settlement", _snapshot(2))


def test_config_apply_result_requires_its_nominal_receipt() -> None:
    with pytest.raises(ObserveContractError, match="ConfigSettlementReceipt"):
        ConfigApplyResult(cast(Never, object()))


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: ConfigSettlementReceipt(cast(Never, ()), _boundary(), _boundary(), "id"), "non-empty"),
        (
            lambda: ConfigSettlementReceipt((cast(Never, object()),), _boundary(), _boundary(), "id"),
            "DeliveryId",
        ),
        (
            lambda: ConfigSettlementReceipt(
                (DeliveryId("same"), DeliveryId("same")),
                _boundary(),
                _boundary(),
                "id",
            ),
            "distinct",
        ),
        (lambda: ConfigSettlementReceipt((DeliveryId("d"),), cast(Never, object()), _boundary(), "id"), "boundaries"),
        (lambda: ConfigSettlementReceipt((DeliveryId("d"),), _boundary(), _boundary(), " id"), "canonical"),
        (lambda: ConfigSettlementReceipt((DeliveryId("d"),), _boundary(), _boundary(), "x" * 257), "256-byte"),
        (lambda: ConfigSettlementReceipt((DeliveryId("d"),), _boundary(), _boundary(), "\ud800"), "UTF-8"),
        (
            lambda: ConfigSettlementReceipt((DeliveryId("d"),), _boundary(), _boundary(), "id", _snapshot(2)),
            "snapshot revision",
        ),
        (
            lambda: ContextAppendReceipt(
                (DeliveryId("d"),),
                _boundary(),
                _boundary(),
                "id",
                NonConfigObservationFamily.USER,
                cast(Never, object()),
            ),
            "snapshot",
        ),
        (
            lambda: ContextAppendReceipt(
                (DeliveryId("d"),),
                _boundary(),
                _boundary(),
                "id",
                cast(Never, object()),
            ),
            "NonConfigObservationFamily",
        ),
    ],
)
def test_settlement_receipts_reject_malformed_provider_evidence(factory: Callable[[], object], message: str) -> None:
    with pytest.raises(ObserveContractError, match=message):
        factory()


def test_batch_receipt_requires_matching_children_and_ack_reference() -> None:
    boundary = _boundary(0, 1)
    config = ConfigSettlementReceipt((DeliveryId("config"),), boundary, boundary, "config")
    context = ContextAppendReceipt(
        (DeliveryId("user"),),
        boundary,
        boundary,
        "context",
        NonConfigObservationFamily.USER,
    )
    both = ObservationBatchReceipt(
        boundary,
        boundary,
        config,
        context,
        DeliveryAckReference("stream", (DeliveryId("config"), DeliveryId("user")), "ack"),
    )
    assert both.delivery_ids == (DeliveryId("config"), DeliveryId("user"))
    with pytest.raises(ObserveContractError, match="config_receipt"):
        ObservationBatchReceipt(
            boundary,
            boundary,
            cast(Never, object()),
            None,
            DeliveryAckReference("stream", (DeliveryId("config"),), "ack"),
        )
    with pytest.raises(ObserveContractError, match="context_receipt"):
        ObservationBatchReceipt(
            boundary,
            boundary,
            None,
            cast(Never, object()),
            DeliveryAckReference("stream", (DeliveryId("user"),), "ack"),
        )
    with pytest.raises(ObserveContractError, match="ack_reference"):
        ObservationBatchReceipt(boundary, boundary, config, None, cast(Never, object()))
    with pytest.raises(ObserveContractError, match="requires a settlement"):
        ObservationBatchReceipt(boundary, boundary, None, None, cast(Never, object()))
    with pytest.raises(ObserveContractError, match="boundaries"):
        ObservationBatchReceipt(
            boundary,
            boundary,
            ConfigSettlementReceipt((DeliveryId("config"),), _boundary(0, 1, 2), _boundary(0, 1, 2), "config"),
            None,
            DeliveryAckReference("stream", (DeliveryId("config"),), "ack"),
        )
    with pytest.raises(ObserveContractError, match="does not match settled"):
        ObservationBatchReceipt(
            boundary,
            boundary,
            config,
            context,
            DeliveryAckReference("stream", (DeliveryId("config"),), "ack"),
        )
    with pytest.raises(ObserveContractError, match="does not match settled"):
        ObservationBatchReceipt(
            boundary,
            boundary,
            config,
            context,
            DeliveryAckReference("stream", (DeliveryId("user"), DeliveryId("other")), "ack"),
        )


def test_result_and_hook_envelopes_validate_closed_variants() -> None:
    delivery = _delivery(0, UserObservation("user"), "user")
    batch = ObservationBatch((delivery,))
    boundary = _boundary(0, 1)
    snapshot = _snapshot()
    frame = ObserveFrame(batch, boundary, snapshot)
    receipt = _receipt_for((DeliveryId("user"),), config=False)
    result = ObserveResult(ObservationKind.USER, (DeliveryId("user"),), boundary.cursor_range, snapshot, receipt)
    get_value = GetObservationStageValue(frame)
    write_value = WriteObservationStageValue(result)
    assert isinstance(get_value, ObserveStageValue)
    assert isinstance(write_value, ObserveStageValue)
    envelope = ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, get_value, _State())
    assert envelope.hook_state == _State()
    with pytest.raises(ObserveContractError, match="current_state"):
        ObserveResult(cast(Never, object()), result.delivery_ids, result.cursor_range, snapshot, receipt)
    with pytest.raises(ObserveContractError, match="settlement evidence"):
        ObserveResult(ObservationKind.CONFIG, result.delivery_ids, result.cursor_range, snapshot, receipt)
    with pytest.raises(ObserveContractError, match="delivery_ids"):
        ObserveResult(ObservationKind.USER, (DeliveryId("other"),), boundary.cursor_range, snapshot, receipt)
    with pytest.raises(ObserveContractError, match="cursor_range"):
        ObserveResult(ObservationKind.USER, result.delivery_ids, CursorRange(_cursor(0), _cursor(0)), snapshot, receipt)
    with pytest.raises(ObserveContractError, match="stage"):
        ObserveHookEnvelope(cast(Never, object()), get_value, _State())
    with pytest.raises(ObserveContractError, match="requires Get"):
        ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, write_value, _State())
    with pytest.raises(ObserveContractError, match="requires Write"):
        ObserveHookEnvelope(ObserveHookStage.AFTER_WRITE_OBSERVATION, get_value, _State())
    with pytest.raises(ObserveContractError, match="concrete"):
        ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, get_value, cast(Never, HookStateProjection()))


def test_outer_result_values_reject_wrong_nested_nominal_types() -> None:
    delivery = _delivery(0, UserObservation("user"), "user")
    batch = ObservationBatch((delivery,))
    boundary = _boundary(0, 1)
    snapshot = _snapshot()
    receipt = _receipt_for((delivery.delivery_id,), config=False)

    with pytest.raises(ObserveContractError, match="delivery ack reference"):
        DeliveryAck(cast(Never, object()))
    with pytest.raises(ObserveContractError, match="cursor"):
        ObserveRequest(cast(Never, object()), _State())
    with pytest.raises(ObserveContractError, match="hook_state"):
        ObserveRequest(_cursor(), cast(Never, object()))
    with pytest.raises(ObserveContractError, match="frame batch"):
        ObserveFrame(cast(Never, object()), boundary, snapshot)
    with pytest.raises(ObserveContractError, match="background_task_snapshot"):
        ObserveFrame(batch, boundary, cast(Never, object()))
    with pytest.raises(ObserveContractError, match="snapshot revision"):
        ObserveFrame(batch, boundary, _snapshot(2))
    with pytest.raises(ObserveContractError, match="cursor_range"):
        ObserveResult(
            ObservationKind.USER,
            (delivery.delivery_id,),
            cast(Never, object()),
            snapshot,
            receipt,
        )
    with pytest.raises(ObserveContractError, match="background_task_snapshot"):
        ObserveResult(
            ObservationKind.USER,
            (delivery.delivery_id,),
            boundary.cursor_range,
            cast(Never, object()),
            receipt,
        )
    with pytest.raises(ObserveContractError, match="observation_receipt"):
        ObserveResult(
            ObservationKind.USER,
            (delivery.delivery_id,),
            boundary.cursor_range,
            snapshot,
            cast(Never, object()),
        )
    with pytest.raises(ObserveContractError, match="task snapshot revision"):
        ObserveResult(
            ObservationKind.USER,
            (delivery.delivery_id,),
            boundary.cursor_range,
            _snapshot(2),
            receipt,
        )
    with pytest.raises(ObserveContractError, match="stage frame"):
        GetObservationStageValue(cast(Never, object()))
    with pytest.raises(ObserveContractError, match="stage result"):
        WriteObservationStageValue(cast(Never, object()))
    with pytest.raises(ObserveContractError, match="known stage value"):
        ObserveHookEnvelope(ObserveHookStage.AFTER_GET_OBSERVATION, cast(Never, ObserveStageValue()), _State())


def test_observation_kind_rejects_a_conflicting_batch() -> None:
    batch = ObservationBatch(
        (
            _delivery(0, UserObservation("user"), "user"),
            _delivery(1, ToolObservation("tool"), "tool"),
        )
    )

    with pytest.raises(ObserveContractError, match="conflicting"):
        observation_kind(batch)

    boundary = _boundary()
    context = ContextAppendReceipt(
        (DeliveryId("user"),),
        boundary,
        boundary,
        "context",
        NonConfigObservationFamily.USER,
    )
    object.__setattr__(context, "family", cast(Never, object()))
    receipt = ObservationBatchReceipt(
        boundary,
        boundary,
        None,
        context,
        DeliveryAckReference("stream", (DeliveryId("user"),), "ack"),
    )
    with pytest.raises(ObserveContractError, match="family is invalid"):
        _ = receipt.current_state


def test_request_frame_result_and_ack_are_frozen_slot_values() -> None:
    delivery = _delivery(0, ConfigObservation("config"), "config")
    batch = ObservationBatch((delivery,))
    boundary = _boundary()
    snapshot = _snapshot()
    frame = ObserveFrame(batch, boundary, snapshot)
    receipt = _receipt_for((DeliveryId("config"),), config=True)
    result = ObserveResult(ObservationKind.CONFIG, (DeliveryId("config"),), boundary.cursor_range, snapshot, receipt)
    request = ObserveRequest(_cursor(), _State())
    ack = DeliveryAck(DeliveryAckReference("stream", (DeliveryId("config"),), "ack"))
    values = (request, frame, result, ack, GetObservationStageValue(frame), WriteObservationStageValue(result))
    for value in values:
        assert "__dict__" not in type(value).__slots__
    with pytest.raises(FrozenInstanceError):
        request.__setattr__("cursor", _cursor(1))

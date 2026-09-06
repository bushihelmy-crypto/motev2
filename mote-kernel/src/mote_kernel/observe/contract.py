"""Immutable values crossing the Observe graph boundary.

Observe deliberately knows nothing about the producers behind the queue.  A
producer adapts its wire value to one of the four nominal observation classes
below; the graph then preserves that value and its delivery coordinates while
the capability Ports perform the provider-owned settlements.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Generic, TypeAlias, TypeVar, cast

from mote_kernel.hooks.contract import HookGraphValue
from mote_kernel.observe.identity import (
    BlockingTaskRef,
    CursorRange,
    DeliveryAckReference,
    DeliveryId,
    ObservationBoundary,
    ObservationCursor,
    ObservationWait,
    ObserveHookStage,
)


class ObserveContractError(ValueError):
    """Raised when a value violates Observe's outer contract."""


class Observation(HookGraphValue):
    """Closed nominal base for the four observation families."""

    __slots__ = ()


class ObservationPayload(HookGraphValue):
    """Nominal base for immutable payload DTOs bound during assembly."""

    __slots__ = ()


ObservationPayloadT = TypeVar("ObservationPayloadT", covariant=True)
ObserveRequestStateT = TypeVar("ObserveRequestStateT", bound="HookStateProjection")


def _is_concrete_hook_state(value: HookGraphValue, /) -> bool:
    """Return whether a graph value is a concrete shared-Hook state projection."""

    return isinstance(value, HookStateProjection) and type(value) is not HookStateProjection


def _require_payload(value: ObservationPayloadT, field_name: str, /) -> ObservationPayloadT:
    if value is None:
        raise ObserveContractError(f"{field_name} is required")
    return value


@dataclass(frozen=True, slots=True)
class ConfigObservation(Observation, Generic[ObservationPayloadT]):
    """A configuration/control-plane observation."""

    payload: ObservationPayloadT

    def __post_init__(self) -> None:
        _require_payload(self.payload, "config observation payload")


@dataclass(frozen=True, slots=True)
class ToolObservation(Observation, Generic[ObservationPayloadT]):
    """A tool call, result, or tool-task lifecycle observation."""

    payload: ObservationPayloadT

    def __post_init__(self) -> None:
        _require_payload(self.payload, "tool observation payload")


@dataclass(frozen=True, slots=True)
class UserObservation(Observation, Generic[ObservationPayloadT]):
    """A user query observation."""

    payload: ObservationPayloadT

    def __post_init__(self) -> None:
        _require_payload(self.payload, "user observation payload")


@dataclass(frozen=True, slots=True)
class AssistantObservation(Observation, Generic[ObservationPayloadT]):
    """A child/assistant result projected at the Observe boundary."""

    payload: ObservationPayloadT

    def __post_init__(self) -> None:
        _require_payload(self.payload, "assistant observation payload")


ObservationValue: TypeAlias = (
    ConfigObservation[ObservationPayloadT]
    | ToolObservation[ObservationPayloadT]
    | UserObservation[ObservationPayloadT]
    | AssistantObservation[ObservationPayloadT]
)


@dataclass(frozen=True, slots=True)
class ObservationDelivery(HookGraphValue, Generic[ObservationPayloadT]):
    """One immutable, idempotent delivery in the durable observation stream."""

    delivery_id: DeliveryId
    cursor_before: ObservationCursor
    cursor_after: ObservationCursor
    payload: ObservationPayloadT
    observation_revision: int

    def __post_init__(self) -> None:
        if type(self.delivery_id) is not DeliveryId:
            raise ObserveContractError("observation delivery delivery_id must be a DeliveryId")
        if type(self.cursor_before) is not ObservationCursor:
            raise ObserveContractError("observation delivery cursor_before must be an ObservationCursor")
        if type(self.cursor_after) is not ObservationCursor:
            raise ObserveContractError("observation delivery cursor_after must be an ObservationCursor")
        if type(self.payload) not in _OBSERVATION_TYPES:
            raise ObserveContractError("observation delivery payload must be one known observation class")
        if self.cursor_before.stream_id != self.cursor_after.stream_id:
            raise ObserveContractError("observation delivery cursors must use one stream")
        if self.cursor_after.sequence <= self.cursor_before.sequence:
            raise ObserveContractError("observation delivery cursor_after must advance")
        if type(self.observation_revision) is not int or self.observation_revision < 0:
            raise ObserveContractError("observation delivery revision must be non-negative")

    @property
    def stream_id(self) -> str:
        """Return the stream identity carried by both cursors."""

        return self.cursor_before.stream_id

    @property
    def cursor_range(self) -> CursorRange:
        """Return this delivery's cursor range."""

        return CursorRange(self.cursor_before, self.cursor_after)


def _validate_delivery_sequence(
    deliveries: tuple[ObservationDelivery[Observation], ...],
    field_name: str,
    /,
) -> None:
    if type(deliveries) is not tuple or not deliveries:
        raise ObserveContractError(f"{field_name} deliveries must be a non-empty tuple")
    if any(type(delivery) is not ObservationDelivery for delivery in deliveries):
        raise ObserveContractError(f"{field_name} deliveries must contain ObservationDelivery values")
    first = deliveries[0]
    for previous, current in pairwise(deliveries):
        if current.stream_id != first.stream_id:
            raise ObserveContractError(f"{field_name} deliveries must use one stream")
        if current.observation_revision != first.observation_revision:
            raise ObserveContractError(f"{field_name} deliveries must use one observation revision")
        # A queue window is a contiguous projection of one durable stream.
        # Allowing a gap or overlap here would let a provider silently skip a
        # delivery or settle one delivery twice while still looking FIFO.
        if current.cursor_before != previous.cursor_after:
            raise ObserveContractError(f"{field_name} deliveries must be contiguous FIFO cursors")


def _delivery_ids(deliveries: tuple[ObservationDelivery[Observation], ...]) -> tuple[DeliveryId, ...]:
    ids = tuple(delivery.delivery_id for delivery in deliveries)
    if len(ids) != len(set(ids)):
        raise ObserveContractError("observation deliveries must have distinct delivery ids")
    return ids


def _is_fifo_subsequence(
    subset: tuple[DeliveryId, ...],
    sequence: tuple[DeliveryId, ...],
    /,
) -> bool:
    """Return whether ``subset`` keeps its order inside ``sequence``.

    A complete batch may interleave Config and Context deliveries, so the
    two child receipts cannot reconstruct the original cross-family merge.
    They can, however, prove that each family retained its own FIFO order;
    this small order check closes that boundary without inventing positions.
    """

    offset = 0
    for expected in subset:
        try:
            offset = sequence.index(expected, offset) + 1
        except ValueError:
            return False
    return True


class ContextObservationBatch(HookGraphValue):
    """Closed nominal base for the three batches written to Context."""

    __slots__ = ()


def _validate_family_batch(
    deliveries: tuple[ObservationDelivery[Observation], ...],
    expected_payload_type: type[Observation],
    field_name: str,
    /,
) -> None:
    # A family batch is a projection of one complete queue window.  Config
    # and a non-Config delivery may be interleaved in that window, so the
    # projected tuple can legitimately contain cursor gaps.  Preserve FIFO
    # order and reject overlap/backtracking, while the enclosing
    # ``ObservationBatch`` enforces contiguity for the complete window.
    if type(deliveries) is not tuple or not deliveries:
        raise ObserveContractError(f"{field_name} deliveries must be a non-empty tuple")
    if any(type(delivery) is not ObservationDelivery for delivery in deliveries):
        raise ObserveContractError(f"{field_name} deliveries must contain ObservationDelivery values")
    first = deliveries[0]
    for previous, current in pairwise(deliveries):
        if current.stream_id != first.stream_id:
            raise ObserveContractError(f"{field_name} deliveries must use one stream")
        if current.observation_revision != first.observation_revision:
            raise ObserveContractError(f"{field_name} deliveries must use one observation revision")
        if current.cursor_before.sequence < previous.cursor_after.sequence:
            raise ObserveContractError(f"{field_name} deliveries must preserve FIFO cursor order")
    _delivery_ids(deliveries)
    if any(type(delivery.payload) is not expected_payload_type for delivery in deliveries):
        raise ObserveContractError(f"{field_name} deliveries contain a different observation family")


@dataclass(frozen=True, slots=True)
class ConfigBatch(HookGraphValue):
    """All Config deliveries in one observation window, in FIFO order."""

    deliveries: tuple[ObservationDelivery[Observation], ...]

    def __post_init__(self) -> None:
        _validate_family_batch(self.deliveries, ConfigObservation, "config batch")

    @property
    def effective(self) -> ConfigObservation[Observation]:
        """Return the last Config value, without dropping earlier evidence."""

        return cast(ConfigObservation[Observation], self.deliveries[-1].payload)

    @property
    def delivery_ids(self) -> tuple[DeliveryId, ...]:
        return _delivery_ids(self.deliveries)


@dataclass(frozen=True, slots=True)
class ToolBatch(ContextObservationBatch):
    """All Tool deliveries in one observation window, in FIFO order."""

    deliveries: tuple[ObservationDelivery[Observation], ...]

    def __post_init__(self) -> None:
        _validate_family_batch(self.deliveries, ToolObservation, "tool batch")

    @property
    def delivery_ids(self) -> tuple[DeliveryId, ...]:
        return _delivery_ids(self.deliveries)


@dataclass(frozen=True, slots=True)
class UserBatch(ContextObservationBatch):
    """All User deliveries in one observation window, in FIFO order."""

    deliveries: tuple[ObservationDelivery[Observation], ...]

    def __post_init__(self) -> None:
        _validate_family_batch(self.deliveries, UserObservation, "user batch")

    @property
    def delivery_ids(self) -> tuple[DeliveryId, ...]:
        return _delivery_ids(self.deliveries)


@dataclass(frozen=True, slots=True)
class AssistantBatch(ContextObservationBatch):
    """All Assistant deliveries in one observation window, in FIFO order."""

    deliveries: tuple[ObservationDelivery[Observation], ...]

    def __post_init__(self) -> None:
        _validate_family_batch(self.deliveries, AssistantObservation, "assistant batch")

    @property
    def delivery_ids(self) -> tuple[DeliveryId, ...]:
        return _delivery_ids(self.deliveries)


@dataclass(frozen=True, slots=True)
class ObservationBatch(HookGraphValue):
    """The complete FIFO window returned by ``ObservationQueuePort``.

    Family-specific batches are derived from ``deliveries`` on access.  The
    tuple is the only stored representation, so Config and a single
    non-Config family cannot drift from the authoritative FIFO window.
    """

    deliveries: tuple[ObservationDelivery[Observation], ...]

    def __post_init__(self) -> None:
        _validate_delivery_sequence(self.deliveries, "observation batch")
        _delivery_ids(self.deliveries)

    @property
    def config(self) -> ConfigBatch | None:
        deliveries = tuple(delivery for delivery in self.deliveries if type(delivery.payload) is ConfigObservation)
        return ConfigBatch(deliveries) if deliveries else None

    @property
    def tool(self) -> ToolBatch | None:
        deliveries = tuple(delivery for delivery in self.deliveries if type(delivery.payload) is ToolObservation)
        return ToolBatch(deliveries) if deliveries else None

    @property
    def user(self) -> UserBatch | None:
        deliveries = tuple(delivery for delivery in self.deliveries if type(delivery.payload) is UserObservation)
        return UserBatch(deliveries) if deliveries else None

    @property
    def assistant(self) -> AssistantBatch | None:
        deliveries = tuple(delivery for delivery in self.deliveries if type(delivery.payload) is AssistantObservation)
        return AssistantBatch(deliveries) if deliveries else None

    @property
    def non_config_families(self) -> tuple[NonConfigObservationFamily, ...]:
        # Preserve first-seen FIFO order.  This order is used in Conflict
        # diagnostics and must not depend on the enum declaration order.
        return _families_for_deliveries(self.deliveries)

    @property
    def delivery_ids(self) -> tuple[DeliveryId, ...]:
        return _delivery_ids(self.deliveries)

    @property
    def stream_id(self) -> str:
        return self.deliveries[0].stream_id

    @property
    def observation_revision(self) -> int:
        return self.deliveries[0].observation_revision


class NonConfigObservationFamily(StrEnum):
    """The mutually exclusive non-Config families."""

    TOOL = "tool"
    USER = "user"
    ASSISTANT = "assistant"


class ObservationKind(StrEnum):
    """The only current-state value exposed to the parent ReAct graph."""

    CONFIG = "config"
    TOOL = "tool"
    USER = "user"
    ASSISTANT = "assistant"


def _kind_for_family(family: NonConfigObservationFamily, /) -> ObservationKind:
    if family is NonConfigObservationFamily.TOOL:
        return ObservationKind.TOOL
    if family is NonConfigObservationFamily.USER:
        return ObservationKind.USER
    if family is NonConfigObservationFamily.ASSISTANT:
        return ObservationKind.ASSISTANT
    raise ObserveContractError("non-Config observation family is invalid")


@dataclass(frozen=True, slots=True)
class ObservationConflict(HookGraphValue):
    """A complete window containing two or more non-Config families."""

    deliveries: tuple[ObservationDelivery[Observation], ...]
    families: tuple[NonConfigObservationFamily, ...]

    def __post_init__(self) -> None:
        _validate_delivery_sequence(self.deliveries, "observation conflict")
        _delivery_ids(self.deliveries)
        if type(self.families) is not tuple or len(self.families) < 2:
            raise ObserveContractError("observation conflict requires at least two families")
        if len(set(self.families)) != len(self.families):
            raise ObserveContractError("observation conflict families must be distinct")
        if any(type(family) is not NonConfigObservationFamily for family in self.families):
            raise ObserveContractError("observation conflict families must be NonConfigObservationFamily values")
        present = set(_families_for_deliveries(self.deliveries))
        if tuple(self.families) != _families_for_deliveries(self.deliveries) or set(self.families) != present:
            raise ObserveContractError("observation conflict families do not match deliveries")

    @property
    def delivery_ids(self) -> tuple[DeliveryId, ...]:
        return _delivery_ids(self.deliveries)

    @property
    def families_for_deliveries(self) -> tuple[NonConfigObservationFamily, ...]:
        """Return the first-seen non-Config families in the conflicted window."""

        return _families_for_deliveries(self.deliveries)


def _families_for_deliveries(
    deliveries: tuple[ObservationDelivery[Observation], ...],
) -> tuple[NonConfigObservationFamily, ...]:
    present: list[NonConfigObservationFamily] = []
    for delivery in deliveries:
        family = (
            NonConfigObservationFamily.TOOL
            if type(delivery.payload) is ToolObservation
            else NonConfigObservationFamily.USER
            if type(delivery.payload) is UserObservation
            else NonConfigObservationFamily.ASSISTANT
            if type(delivery.payload) is AssistantObservation
            else None
        )
        if family is not None and family not in present:
            present.append(family)
    return tuple(present)


class ObservationRead(HookGraphValue):
    """Closed result family returned by the queue read capability."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Available(ObservationRead):
    """A non-empty complete FIFO observation window."""

    batch: ObservationBatch
    boundary: ObservationBoundary

    def __post_init__(self) -> None:
        # Check the outer nominal type before dereferencing ``deliveries``.
        # Queue results cross an asynchronous/provider boundary and may be
        # forged by a deserializer; malformed values must stay inside
        # Observe's contract vocabulary rather than leaking AttributeError.
        if type(self.batch) is not ObservationBatch:
            raise ObserveContractError("available batch must be an ObservationBatch")
        _validate_boundary_for_deliveries(self.boundary, self.batch.deliveries, "available")
        if len(self.batch.non_config_families) > 1:
            raise ObserveContractError("available cannot contain conflicting non-Config families")


@dataclass(frozen=True, slots=True)
class Empty(ObservationRead):
    """No delivery is available; ``wait`` is the durable wake coordinate."""

    wait: ObservationWait
    boundary: ObservationBoundary

    def __post_init__(self) -> None:
        if type(self.wait) is not ObservationWait:
            raise ObserveContractError("empty read wait must be an ObservationWait")
        if type(self.boundary) is not ObservationBoundary:
            raise ObserveContractError("empty read boundary must be an ObservationBoundary")
        if self.wait.stream_id != self.boundary.stream_id:
            raise ObserveContractError("empty read wait and boundary must use one stream")
        if self.wait.observation_revision != self.boundary.observation_revision:
            raise ObserveContractError("empty read wait and boundary must use one revision")
        # An empty read cannot consume a delivery.  Requiring an identical
        # before/after cursor is important: otherwise a malformed provider
        # could make Observe skip an unseen delivery while still returning a
        # wake coordinate that looks valid.
        if self.boundary.cursor_before != self.boundary.cursor_after:
            raise ObserveContractError("empty read boundary cannot advance its cursor")
        if self.wait.after_cursor != self.boundary.cursor_after:
            raise ObserveContractError("empty read wait must begin after the returned boundary")


@dataclass(frozen=True, slots=True)
class Conflict(ObservationRead):
    """A complete window that violates non-Config family exclusivity."""

    conflict: ObservationConflict
    boundary: ObservationBoundary

    def __post_init__(self) -> None:
        # As with ``Available``, validate the nested closed value before
        # touching its fields so malformed provider/deserialized values fail
        # deterministically with ObserveContractError.
        if type(self.conflict) is not ObservationConflict:
            raise ObserveContractError("conflict value must be an ObservationConflict")
        _validate_boundary_for_deliveries(self.boundary, self.conflict.deliveries, "conflict")


def _validate_boundary_for_deliveries(
    boundary: ObservationBoundary,
    deliveries: tuple[ObservationDelivery[Observation], ...],
    field_name: str,
    /,
) -> None:
    if type(boundary) is not ObservationBoundary:
        raise ObserveContractError(f"{field_name} boundary must be an ObservationBoundary")
    _validate_delivery_sequence(deliveries, field_name)
    first = deliveries[0]
    last = deliveries[-1]
    if (
        boundary.stream_id != first.stream_id
        or boundary.cursor_before != first.cursor_before
        or boundary.cursor_after != last.cursor_after
        or boundary.observation_revision != first.observation_revision
    ):
        raise ObserveContractError(f"{field_name} boundary does not match its deliveries")


@dataclass(frozen=True, slots=True)
class BackgroundTaskSnapshot(HookGraphValue):
    """Immutable blocking-task evidence captured at one observation revision."""

    observation_revision: int
    blocking_tasks: tuple[BlockingTaskRef, ...]

    def __post_init__(self) -> None:
        if type(self.observation_revision) is not int or self.observation_revision < 0:
            raise ObserveContractError("background task snapshot revision must be non-negative")
        if type(self.blocking_tasks) is not tuple:
            raise ObserveContractError("background task snapshot blocking_tasks must be a tuple")
        if any(type(task) is not BlockingTaskRef for task in self.blocking_tasks):
            raise ObserveContractError("background task snapshot contains an invalid task reference")
        keys = tuple((task.task_id, task.incarnation) for task in self.blocking_tasks)
        if len(keys) != len(set(keys)):
            raise ObserveContractError("background task snapshot cannot repeat a task incarnation")
        object.__setattr__(
            self,
            "blocking_tasks",
            tuple(
                sorted(
                    self.blocking_tasks,
                    key=lambda task: (task.task_id, task.incarnation, task.completion_policy),
                )
            ),
        )

    @property
    def has_blocking_tasks(self) -> bool:
        """Whether the parent ReAct completion gate must remain blocked."""

        return bool(self.blocking_tasks)


def _validate_delivery_ids(
    delivery_ids: tuple[DeliveryId, ...],
    field_name: str,
    /,
) -> None:
    if type(delivery_ids) is not tuple or not delivery_ids:
        raise ObserveContractError(f"{field_name} must be a non-empty tuple")
    if any(type(delivery_id) is not DeliveryId for delivery_id in delivery_ids):
        raise ObserveContractError(f"{field_name} must contain DeliveryId values")
    if len(set(delivery_ids)) != len(delivery_ids):
        raise ObserveContractError(f"{field_name} must contain distinct delivery ids")


@dataclass(frozen=True, slots=True)
class ConfigSettlementReceipt(HookGraphValue):
    """Receipt for applying the Config subset of one observation window."""

    delivery_ids: tuple[DeliveryId, ...]
    read_boundary: ObservationBoundary
    settlement_boundary: ObservationBoundary
    settlement_id: str
    background_task_snapshot: BackgroundTaskSnapshot | None = None

    def __post_init__(self) -> None:
        _validate_delivery_ids(self.delivery_ids, "config settlement delivery_ids")
        validate_settlement_boundary(self.read_boundary, self.settlement_boundary, "config settlement")
        _require_identifier(self.settlement_id, "config settlement settlement_id")
        _validate_settlement_snapshot(
            self.background_task_snapshot,
            self.read_boundary,
            self.settlement_boundary,
            "config settlement",
        )


@dataclass(frozen=True, slots=True)
class ContextAppendReceipt(HookGraphValue):
    """Receipt for appending one non-Config family to Context."""

    delivery_ids: tuple[DeliveryId, ...]
    read_boundary: ObservationBoundary
    settlement_boundary: ObservationBoundary
    settlement_id: str
    family: NonConfigObservationFamily
    background_task_snapshot: BackgroundTaskSnapshot | None = None

    def __post_init__(self) -> None:
        _validate_delivery_ids(self.delivery_ids, "context append delivery_ids")
        validate_settlement_boundary(self.read_boundary, self.settlement_boundary, "context append")
        _require_identifier(self.settlement_id, "context append settlement_id")
        if type(self.family) is not NonConfigObservationFamily:
            raise ObserveContractError("context append family must be a NonConfigObservationFamily")
        _validate_settlement_snapshot(
            self.background_task_snapshot,
            self.read_boundary,
            self.settlement_boundary,
            "context append",
        )


def _require_identifier(value: str, field_name: str, /) -> None:
    if type(value) is not str or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ObserveContractError(f"{field_name} must be a canonical non-empty string")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ObserveContractError(f"{field_name} must be valid UTF-8") from error
    if encoded_length > 256:
        raise ObserveContractError(f"{field_name} exceeds its 256-byte limit")


def validate_settlement_boundary(
    read_boundary: ObservationBoundary,
    settlement_boundary: ObservationBoundary,
    field_name: str,
    /,
) -> None:
    """Validate the only two legal settlement-boundary relationships.

    A provider either settles at the exact read boundary or returns a newer
    revision anchored at the batch end.  Keeping this relation here gives
    receipt construction and the write node one shared source of truth.
    """

    if type(read_boundary) is not ObservationBoundary or type(settlement_boundary) is not ObservationBoundary:
        raise ObserveContractError(f"{field_name} boundaries must be ObservationBoundary values")
    if read_boundary.stream_id != settlement_boundary.stream_id:
        raise ObserveContractError(f"{field_name} boundaries must use one stream")
    if settlement_boundary.observation_revision < read_boundary.observation_revision:
        raise ObserveContractError(f"{field_name} settlement revision cannot move backwards")

    if settlement_boundary.observation_revision == read_boundary.observation_revision:
        # A settlement at the read revision cannot silently change the queue
        # coordinate.  Accepting a different cursor here would allow a
        # provider to claim that deliveries beyond the observed batch were
        # settled without returning their delivery evidence.
        if settlement_boundary != read_boundary:
            raise ObserveContractError(
                f"{field_name} settlement boundary must equal its read boundary when revision is unchanged"
            )
        return

    # A revision successor is a zero-length boundary at the end of the batch.
    # The provider may advance its durable revision while committing the
    # observed deliveries, but it may not advance the stream cursor past the
    # batch (or retain the old range) without evidence for those deliveries.
    if (
        settlement_boundary.cursor_before != read_boundary.cursor_after
        or settlement_boundary.cursor_after != read_boundary.cursor_after
    ):
        raise ObserveContractError(f"{field_name} successor boundary must be zero-length at the read cursor_after")


def _validate_settlement_snapshot(
    snapshot: BackgroundTaskSnapshot | None,
    read_boundary: ObservationBoundary,
    settlement_boundary: ObservationBoundary,
    field_name: str,
    /,
) -> None:
    if settlement_boundary.observation_revision > read_boundary.observation_revision and snapshot is None:
        raise ObserveContractError(f"{field_name} successor boundary requires a background task snapshot")
    if snapshot is not None:
        if type(snapshot) is not BackgroundTaskSnapshot:
            raise ObserveContractError(f"{field_name} snapshot has an invalid type")
        if snapshot.observation_revision != settlement_boundary.observation_revision:
            raise ObserveContractError(f"{field_name} snapshot revision does not match settlement boundary")


@dataclass(frozen=True, slots=True)
class ObservationBatchReceipt(HookGraphValue):
    """The complete settlement evidence for one Observe activation."""

    read_boundary: ObservationBoundary
    settlement_boundary: ObservationBoundary
    config_receipt: ConfigSettlementReceipt | None
    context_receipt: ContextAppendReceipt | None
    ack_reference: DeliveryAckReference

    def __post_init__(self) -> None:
        validate_settlement_boundary(self.read_boundary, self.settlement_boundary, "observation batch receipt")
        if self.config_receipt is None and self.context_receipt is None:
            raise ObserveContractError("observation batch receipt requires a settlement receipt")
        if self.config_receipt is not None and type(self.config_receipt) is not ConfigSettlementReceipt:
            raise ObserveContractError("observation batch config_receipt has an invalid type")
        if self.context_receipt is not None and type(self.context_receipt) is not ContextAppendReceipt:
            raise ObserveContractError("observation batch context_receipt has an invalid type")
        for receipt in (self.config_receipt, self.context_receipt):
            if receipt is None:
                continue
            if receipt.read_boundary != self.read_boundary or receipt.settlement_boundary != self.settlement_boundary:
                raise ObserveContractError("observation batch receipt boundaries do not match child receipts")
        if type(self.ack_reference) is not DeliveryAckReference:
            raise ObserveContractError("observation batch ack_reference has an invalid type")
        child_ids = tuple(
            delivery_id
            for receipt in (self.config_receipt, self.context_receipt)
            if receipt is not None
            for delivery_id in receipt.delivery_ids
        )
        if (
            self.ack_reference.stream_id != self.read_boundary.stream_id
            or len(set(child_ids)) != len(child_ids)
            or set(self.ack_reference.delivery_ids) != set(child_ids)
        ):
            raise ObserveContractError("observation batch ack_reference does not match settled deliveries")
        for receipt in (self.config_receipt, self.context_receipt):
            if receipt is None:
                continue
            if not _is_fifo_subsequence(receipt.delivery_ids, self.ack_reference.delivery_ids):
                raise ObserveContractError("observation batch ack_reference does not preserve family FIFO order")

    @property
    def delivery_ids(self) -> tuple[DeliveryId, ...]:
        return self.ack_reference.delivery_ids

    @property
    def current_state(self) -> ObservationKind:
        """Derive the parent-facing state from settled Context evidence."""

        if self.context_receipt is None:
            return ObservationKind.CONFIG
        return _kind_for_family(self.context_receipt.family)


@dataclass(frozen=True, slots=True)
class DeliveryAck(HookGraphValue):
    """Provider confirmation for an idempotent delivery acknowledgement."""

    reference: DeliveryAckReference

    def __post_init__(self) -> None:
        if type(self.reference) is not DeliveryAckReference:
            raise ObserveContractError("delivery ack reference must be a DeliveryAckReference")


@dataclass(frozen=True, slots=True)
class ObserveRequest(HookGraphValue, Generic[ObserveRequestStateT]):
    """Input to one Observe nested-graph activation."""

    cursor: ObservationCursor
    hook_state: ObserveRequestStateT

    def __post_init__(self) -> None:
        if type(self.cursor) is not ObservationCursor:
            raise ObserveContractError("observe request cursor must be an ObservationCursor")
        state: HookGraphValue = self.hook_state
        if not _is_concrete_hook_state(state):
            raise ObserveContractError("observe request hook_state must be a concrete HookStateProjection")


@dataclass(frozen=True, slots=True)
class ObserveFrame(HookGraphValue):
    """The immutable hand-off from get_observation to write_observation."""

    batch: ObservationBatch
    boundary: ObservationBoundary
    background_task_snapshot: BackgroundTaskSnapshot

    def __post_init__(self) -> None:
        if type(self.batch) is not ObservationBatch:
            raise ObserveContractError("observe frame batch must be an ObservationBatch")
        _validate_boundary_for_deliveries(self.boundary, self.batch.deliveries, "observe frame")
        if len(self.batch.non_config_families) > 1:
            raise ObserveContractError("observe frame cannot contain conflicting non-Config families")
        if type(self.background_task_snapshot) is not BackgroundTaskSnapshot:
            raise ObserveContractError("observe frame background_task_snapshot has an invalid type")
        if self.background_task_snapshot.observation_revision != self.boundary.observation_revision:
            raise ObserveContractError("observe frame task snapshot revision does not match boundary")


def observation_kind(batch: ObservationBatch, /) -> ObservationKind:
    """Map a validated batch to its parent-facing four-value state."""

    if type(batch) is not ObservationBatch:
        raise ObserveContractError("observation_kind requires an ObservationBatch")
    families = batch.non_config_families
    if not families:
        return ObservationKind.CONFIG
    if len(families) != 1:
        raise ObserveContractError("a conflicting batch cannot produce an ObservationKind")
    return _kind_for_family(families[0])


@dataclass(frozen=True, slots=True)
class ObserveResult(HookGraphValue):
    """The immutable projection returned by Observe to its parent graph."""

    current_state: ObservationKind
    delivery_ids: tuple[DeliveryId, ...]
    cursor_range: CursorRange
    background_task_snapshot: BackgroundTaskSnapshot
    observation_receipt: ObservationBatchReceipt

    def __post_init__(self) -> None:
        if type(self.current_state) is not ObservationKind:
            raise ObserveContractError("observe result current_state must be an ObservationKind")
        _validate_delivery_ids(self.delivery_ids, "observe result delivery_ids")
        if type(self.cursor_range) is not CursorRange:
            raise ObserveContractError("observe result cursor_range must be a CursorRange")
        if type(self.background_task_snapshot) is not BackgroundTaskSnapshot:
            raise ObserveContractError("observe result background_task_snapshot has an invalid type")
        if type(self.observation_receipt) is not ObservationBatchReceipt:
            raise ObserveContractError("observe result observation_receipt has an invalid type")
        if self.current_state is not self.observation_receipt.current_state:
            raise ObserveContractError("observe result current_state does not match settlement evidence")
        if self.delivery_ids != self.observation_receipt.delivery_ids:
            raise ObserveContractError("observe result delivery_ids do not match observation_receipt")
        if self.cursor_range != self.observation_receipt.settlement_boundary.cursor_range:
            raise ObserveContractError("observe result cursor_range does not match settlement boundary")
        if (
            self.background_task_snapshot.observation_revision
            != self.observation_receipt.settlement_boundary.observation_revision
        ):
            raise ObserveContractError("observe result task snapshot revision does not match receipt")
        receipt_snapshots = tuple(
            receipt.background_task_snapshot
            for receipt in (
                self.observation_receipt.config_receipt,
                self.observation_receipt.context_receipt,
            )
            if receipt is not None and receipt.background_task_snapshot is not None
        )
        if any(snapshot != self.background_task_snapshot for snapshot in receipt_snapshots):
            raise ObserveContractError("observe result task snapshot does not match settlement evidence")


class ObserveStageValue(HookGraphValue):
    """Closed nominal base for the two shared-Hook stage payloads."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class GetObservationStageValue(ObserveStageValue):
    """Payload entering the shared Hook after the read stage."""

    frame: ObserveFrame

    def __post_init__(self) -> None:
        if type(self.frame) is not ObserveFrame:
            raise ObserveContractError("get-observation stage frame must be an ObserveFrame")


@dataclass(frozen=True, slots=True)
class WriteObservationStageValue(ObserveStageValue):
    """Payload entering the shared Hook after the write stage."""

    result: ObserveResult

    def __post_init__(self) -> None:
        if type(self.result) is not ObserveResult:
            raise ObserveContractError("write-observation stage result must be an ObserveResult")


class HookStateProjection(HookGraphValue):
    """Nominal base for the immutable state supplied to the shared Hook."""

    __slots__ = ()


class ObserveHookCommand(HookGraphValue):
    """Nominal base for opaque commands returned by the shared Hook."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class ObserveHookEnvelope(HookGraphValue):
    """One outer envelope shared by both Observe Hook activations."""

    stage: ObserveHookStage
    payload: ObserveStageValue
    hook_state: HookStateProjection

    def __post_init__(self) -> None:
        if type(self.stage) is not ObserveHookStage:
            raise ObserveContractError("observe Hook envelope stage must be an ObserveHookStage")
        if type(self.payload) not in (GetObservationStageValue, WriteObservationStageValue):
            raise ObserveContractError("observe Hook envelope payload must be a known stage value")
        if self.stage is ObserveHookStage.AFTER_GET_OBSERVATION and type(self.payload) is not GetObservationStageValue:
            raise ObserveContractError("after-get-observation envelope requires GetObservationStageValue")
        if (
            self.stage is ObserveHookStage.AFTER_WRITE_OBSERVATION
            and type(self.payload) is not WriteObservationStageValue
        ):
            raise ObserveContractError("after-write-observation envelope requires WriteObservationStageValue")
        state: HookGraphValue = self.hook_state
        if not _is_concrete_hook_state(state):
            raise ObserveContractError("observe Hook envelope hook_state must be a concrete HookStateProjection")


_OBSERVATION_TYPES: tuple[type[Observation], ...] = (
    ConfigObservation,
    ToolObservation,
    UserObservation,
    AssistantObservation,
)


__all__ = [
    "AssistantBatch",
    "AssistantObservation",
    "Available",
    "BackgroundTaskSnapshot",
    "ConfigBatch",
    "ConfigObservation",
    "ConfigSettlementReceipt",
    "Conflict",
    "ContextAppendReceipt",
    "ContextObservationBatch",
    "DeliveryAck",
    "Empty",
    "GetObservationStageValue",
    "HookStateProjection",
    "NonConfigObservationFamily",
    "Observation",
    "ObservationBatch",
    "ObservationBatchReceipt",
    "ObservationConflict",
    "ObservationDelivery",
    "ObservationKind",
    "ObservationPayload",
    "ObservationRead",
    "ObservationValue",
    "ObserveContractError",
    "ObserveFrame",
    "ObserveHookCommand",
    "ObserveHookEnvelope",
    "ObserveRequest",
    "ObserveResult",
    "ObserveStageValue",
    "ToolBatch",
    "ToolObservation",
    "UserBatch",
    "UserObservation",
    "WriteObservationStageValue",
    "observation_kind",
]

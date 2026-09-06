"""Stable identities used by the Observe graph boundary.

The Observe package does not own a queue, task registry, or persistence
store.  These small immutable values only describe coordinates exchanged with
those providers and the two internal stages of the shared Hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from mote_kernel.hooks.contract import HookGraphValue

_IDENTITY_MAX_BYTES = 256


class ObserveIdentityError(ValueError):
    """Raised when an Observe identity or recovery coordinate is malformed."""


def _require_text(value: str, field: str, /, *, maximum: int = _IDENTITY_MAX_BYTES) -> None:
    if type(value) is not str or not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ObserveIdentityError(f"{field} must be a non-empty trimmed single-line string")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ObserveIdentityError(f"{field} must be valid UTF-8") from error
    if length > maximum:
        raise ObserveIdentityError(f"{field} exceeds its {maximum}-byte limit")


def _require_revision(value: int, field: str, /) -> None:
    if type(value) is not int or value < 0:
        raise ObserveIdentityError(f"{field} must be an exact non-negative integer")


@dataclass(frozen=True, slots=True)
class DeliveryId(HookGraphValue):
    """Stable idempotency identity for one queue delivery."""

    value: str

    def __post_init__(self) -> None:
        _require_text(self.value, "delivery id")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ObservationCursor(HookGraphValue):
    """A position in one durable observation stream."""

    stream_id: str
    sequence: int

    def __post_init__(self) -> None:
        _require_text(self.stream_id, "observation cursor stream_id")
        _require_revision(self.sequence, "observation cursor sequence")


@dataclass(frozen=True, slots=True)
class ObservationBoundary(HookGraphValue):
    """The atomic queue read boundary used for task snapshot correlation."""

    stream_id: str
    cursor_before: ObservationCursor
    cursor_after: ObservationCursor
    observation_revision: int

    def __post_init__(self) -> None:
        _require_text(self.stream_id, "observation boundary stream_id")
        if type(self.cursor_before) is not ObservationCursor:
            raise ObserveIdentityError("observation boundary cursor_before must be an ObservationCursor")
        if type(self.cursor_after) is not ObservationCursor:
            raise ObserveIdentityError("observation boundary cursor_after must be an ObservationCursor")
        if self.cursor_before.stream_id != self.stream_id or self.cursor_after.stream_id != self.stream_id:
            raise ObserveIdentityError("observation boundary cursors must use its stream_id")
        if self.cursor_after.sequence < self.cursor_before.sequence:
            raise ObserveIdentityError("observation boundary cursor_after cannot move backwards")
        _require_revision(self.observation_revision, "observation boundary revision")

    @property
    def cursor_range(self) -> CursorRange:
        """Return the immutable cursor range represented by this boundary."""

        return CursorRange(self.cursor_before, self.cursor_after)


@dataclass(frozen=True, slots=True)
class CursorRange(HookGraphValue):
    """The inclusive/exclusive cursor range consumed by one observation batch."""

    before: ObservationCursor
    after: ObservationCursor

    def __post_init__(self) -> None:
        if type(self.before) is not ObservationCursor:
            raise ObserveIdentityError("cursor range before must be an ObservationCursor")
        if type(self.after) is not ObservationCursor:
            raise ObserveIdentityError("cursor range after must be an ObservationCursor")
        if self.before.stream_id != self.after.stream_id:
            raise ObserveIdentityError("cursor range cursors must use the same stream")
        if self.after.sequence < self.before.sequence:
            raise ObserveIdentityError("cursor range after cannot move backwards")


@dataclass(frozen=True, slots=True)
class BlockingTaskRef(HookGraphValue):
    """One blocking task incarnation observed at a queue boundary."""

    task_id: str
    incarnation: int
    completion_policy: str

    def __post_init__(self) -> None:
        _require_text(self.task_id, "blocking task id")
        _require_revision(self.incarnation, "blocking task incarnation")
        _require_text(self.completion_policy, "blocking task completion_policy")


@dataclass(frozen=True, slots=True)
class ObservationWait(HookGraphValue):
    """A durable wait coordinate; it never contains an Event/Future/task."""

    MAX_ENCODED_BYTES: ClassVar[int] = 4_096

    stream_id: str
    after_cursor: ObservationCursor
    observation_revision: int
    wake_condition: str

    def __post_init__(self) -> None:
        _require_text(self.stream_id, "observation wait stream_id")
        if type(self.after_cursor) is not ObservationCursor:
            raise ObserveIdentityError("observation wait after_cursor must be an ObservationCursor")
        if self.after_cursor.stream_id != self.stream_id:
            raise ObserveIdentityError("observation wait cursor must use its stream_id")
        _require_revision(self.observation_revision, "observation wait revision")
        _require_text(self.wake_condition, "observation wait wake_condition")

    def encode(self) -> bytes:
        """Encode only the recovery coordinate for ``Graph.interrupt``."""

        fields = (
            "mote.observe.wait.v1",
            self.stream_id,
            str(self.after_cursor.sequence),
            str(self.observation_revision),
            self.wake_condition,
        )
        encoded_fields = tuple(field.encode("utf-8") for field in fields)
        # Prefix lengths are byte lengths, not Python character counts.  The
        # interrupt codec is UTF-8 and must round-trip stream/wake identities
        # containing non-ASCII text as well as ASCII identifiers.
        encoded = b"".join(f"{len(field)}:".encode("ascii") + field for field in encoded_fields)
        if len(encoded) > self.MAX_ENCODED_BYTES:
            # The individual identity fields are bounded, but retain an
            # explicit aggregate guard so the interrupt boundary remains
            # stable if those limits ever change.
            raise ObserveIdentityError("observation wait encoding exceeds its size limit")
        return encoded

    @classmethod
    def decode(cls, payload: bytes, /) -> ObservationWait:
        """Decode and strictly validate a wait coordinate from interrupt bytes.

        The format is the length-prefixed sequence emitted by :meth:`encode`.
        Parsing is deliberately local and deterministic; no provider state or
        in-memory token table is consulted when a process resumes.
        """

        if type(payload) is not bytes:
            raise ObserveIdentityError("observation wait payload must be bytes")
        if len(payload) > cls.MAX_ENCODED_BYTES:
            raise ObserveIdentityError("observation wait payload exceeds its size limit")
        fields: list[str] = []
        offset = 0
        for _index in range(5):
            separator = payload.find(b":", offset)
            if separator <= offset:
                raise ObserveIdentityError("observation wait payload has an invalid length prefix")
            length_bytes = payload[offset:separator]
            # ``bytes.isdigit`` accepts non-ASCII Unicode digits after a
            # decode/round-trip in some callers.  The wire format is ASCII;
            # keeping this check explicit also guarantees ``int`` cannot
            # leak a ValueError for a malformed prefix.
            if not length_bytes or any(byte < 48 or byte > 57 for byte in length_bytes):
                raise ObserveIdentityError("observation wait payload has a non-numeric length prefix")
            length = int(length_bytes)
            if str(length).encode("ascii") != length_bytes:
                raise ObserveIdentityError("observation wait payload has a non-canonical length prefix")
            start = separator + 1
            end = start + length
            if end > len(payload):
                raise ObserveIdentityError("observation wait payload is truncated")
            try:
                fields.append(payload[start:end].decode("utf-8"))
            except UnicodeDecodeError as error:
                raise ObserveIdentityError("observation wait payload is not valid UTF-8") from error
            offset = end
        if offset != len(payload):
            raise ObserveIdentityError("observation wait payload has trailing bytes")
        marker, stream_id, sequence_text, revision_text, wake_condition = fields
        if marker != "mote.observe.wait.v1":
            raise ObserveIdentityError("observation wait payload has an unknown codec marker")
        sequence = _parse_canonical_ascii_integer(
            sequence_text,
            "observation wait payload has an invalid cursor sequence",
        )
        revision = _parse_canonical_ascii_integer(
            revision_text,
            "observation wait payload has an invalid observation revision",
        )
        return cls(
            stream_id,
            ObservationCursor(stream_id, sequence),
            revision,
            wake_condition,
        )


def _parse_canonical_ascii_integer(value: str, error_message: str, /) -> int:
    """Parse a canonical non-negative decimal without leaking parser errors."""

    if not value or any(character < "0" or character > "9" for character in value):
        raise ObserveIdentityError(error_message)
    if len(value) > 1 and value.startswith("0"):
        raise ObserveIdentityError(error_message)
    return int(value)


@dataclass(frozen=True, slots=True)
class WaitRegistration(HookGraphValue):
    """Provider-owned identity returned after atomic wait registration."""

    wait_id: str
    stream_id: str
    after_cursor: ObservationCursor
    registration_revision: int

    def __post_init__(self) -> None:
        _require_text(self.wait_id, "wait registration wait_id")
        _require_text(self.stream_id, "wait registration stream_id")
        if type(self.after_cursor) is not ObservationCursor:
            raise ObserveIdentityError("wait registration after_cursor must be an ObservationCursor")
        if self.after_cursor.stream_id != self.stream_id:
            raise ObserveIdentityError("wait registration cursor must use its stream_id")
        _require_revision(self.registration_revision, "wait registration revision")


@dataclass(frozen=True, slots=True)
class DeliveryAckReference(HookGraphValue):
    """The exact deliveries acknowledged after Graph settlement."""

    stream_id: str
    delivery_ids: tuple[DeliveryId, ...]
    settlement_id: str

    def __post_init__(self) -> None:
        _require_text(self.stream_id, "delivery ack stream_id")
        if type(self.delivery_ids) is not tuple or not self.delivery_ids:
            raise ObserveIdentityError("delivery ack delivery_ids must be a non-empty tuple")
        if any(type(value) is not DeliveryId for value in self.delivery_ids):
            raise ObserveIdentityError("delivery ack delivery_ids must contain DeliveryId values")
        if len(set(self.delivery_ids)) != len(self.delivery_ids):
            raise ObserveIdentityError("delivery ack delivery_ids must be distinct")
        _require_text(self.settlement_id, "delivery ack settlement_id")


class ObserveHookStage(StrEnum):
    """The stage value entering the one shared Observe Hook."""

    AFTER_GET_OBSERVATION = "after_get_observation"
    AFTER_WRITE_OBSERVATION = "after_write_observation"


__all__ = [
    "BlockingTaskRef",
    "CursorRange",
    "DeliveryAckReference",
    "DeliveryId",
    "ObservationBoundary",
    "ObservationCursor",
    "ObservationWait",
    "ObserveHookStage",
    "ObserveIdentityError",
    "WaitRegistration",
]

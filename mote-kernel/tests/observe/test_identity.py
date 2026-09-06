"""Value-level tests for Observe's durable identities and resume coordinates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Never, cast

import pytest

from mote_kernel.observe.identity import (
    BlockingTaskRef,
    CursorRange,
    DeliveryAckReference,
    DeliveryId,
    ObservationBoundary,
    ObservationCursor,
    ObservationWait,
    ObserveIdentityError,
    WaitRegistration,
)


def _cursor(sequence: int = 0, stream: str = "stream") -> ObservationCursor:
    return ObservationCursor(stream, sequence)


def _boundary(before: int = 0, after: int = 1, revision: int = 1) -> ObservationBoundary:
    return ObservationBoundary("stream", _cursor(before), _cursor(after), revision)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: DeliveryId(cast(Never, None)), "delivery id"),
        (lambda: DeliveryId(" leading"), "delivery id"),
        (lambda: DeliveryId("trailing "), "delivery id"),
        (lambda: DeliveryId("line\nfeed"), "delivery id"),
        (lambda: DeliveryId("x" * 257), "256-byte"),
        (lambda: DeliveryId("\ud800"), "UTF-8"),
        (lambda: ObservationCursor(cast(Never, None), 0), "stream_id"),
        (lambda: ObservationCursor("stream", cast(Never, True)), "sequence"),
        (lambda: ObservationCursor("stream", -1), "sequence"),
        (lambda: ObservationBoundary("stream", cast(Never, None), _cursor(1), 1), "cursor_before"),
        (lambda: ObservationBoundary("stream", _cursor(), cast(Never, None), 1), "cursor_after"),
        (lambda: ObservationBoundary("stream", _cursor(), _cursor(1, "other"), 1), "stream_id"),
        (lambda: ObservationBoundary("stream", _cursor(2), _cursor(1), 1), "backwards"),
        (lambda: ObservationBoundary("stream", _cursor(), _cursor(1), cast(Never, True)), "revision"),
        (lambda: BlockingTaskRef(cast(Never, None), 1, "wait"), "task id"),
        (lambda: BlockingTaskRef("task", cast(Never, True), "wait"), "incarnation"),
        (lambda: BlockingTaskRef("task", 1, ""), "completion_policy"),
        (lambda: ObservationWait("stream", cast(Never, None), 1, "delivery"), "after_cursor"),
        (lambda: ObservationWait("stream", _cursor(0, "other"), 1, "delivery"), "stream_id"),
        (lambda: ObservationWait("stream", _cursor(), cast(Never, True), "delivery"), "revision"),
        (lambda: ObservationWait("stream", _cursor(), 1, ""), "wake_condition"),
        (lambda: WaitRegistration("wait", "stream", cast(Never, None), 1), "after_cursor"),
        (lambda: WaitRegistration("wait", "stream", _cursor(0, "other"), 1), "stream_id"),
        (lambda: WaitRegistration("wait", "stream", _cursor(), cast(Never, True)), "revision"),
        (lambda: DeliveryAckReference("stream", cast(Never, None), "settlement"), "delivery_ids"),
        (lambda: DeliveryAckReference("stream", (cast(Never, object()),), "settlement"), "DeliveryId"),
        (lambda: DeliveryAckReference("stream", (DeliveryId("same"), DeliveryId("same")), "settlement"), "distinct"),
        (lambda: DeliveryAckReference("stream", (DeliveryId("one"),), ""), "settlement_id"),
    ],
)
def test_identity_values_reject_noncanonical_fields(factory: object, message: str) -> None:
    with pytest.raises(ObserveIdentityError, match=message):
        cast(Callable[[], object], factory)()


def test_identity_values_accept_zero_and_falsy_coordinates() -> None:
    assert ObservationCursor("stream", 0).sequence == 0
    assert ObservationBoundary("stream", _cursor(), _cursor(), 0).cursor_range == CursorRange(_cursor(), _cursor())
    assert BlockingTaskRef("task", 0, "wait").incarnation == 0
    assert ObservationWait("stream", _cursor(), 0, "delivery").observation_revision == 0
    assert DeliveryAckReference("stream", (DeliveryId("delivery"),), "settlement").delivery_ids


def test_cursor_range_rejects_wrong_types_and_backward_coordinates() -> None:
    with pytest.raises(ObserveIdentityError, match="before"):
        CursorRange(cast(Never, object()), _cursor())
    with pytest.raises(ObserveIdentityError, match="after"):
        CursorRange(_cursor(), cast(Never, object()))
    with pytest.raises(ObserveIdentityError, match="same stream"):
        CursorRange(_cursor(), _cursor(1, "other"))
    with pytest.raises(ObserveIdentityError, match="backwards"):
        CursorRange(_cursor(2), _cursor(1))


def test_boundary_and_cursor_ranges_expose_immutable_ranges() -> None:
    boundary = _boundary(2, 4, 7)
    expected = CursorRange(_cursor(2), _cursor(4))
    assert boundary.cursor_range == expected
    assert DeliveryId("delivery").__str__() == "delivery"
    with pytest.raises(FrozenInstanceError):
        boundary.stream_id = "replacement"  # type: ignore[misc]


def test_wait_codec_is_length_prefixed_and_rejects_nonbytes_or_oversized_values() -> None:
    wait = ObservationWait("stream", _cursor(12), 3, "delivery")
    encoded = wait.encode()
    assert ObservationWait.decode(encoded) == wait
    with pytest.raises(ObserveIdentityError, match="must be bytes"):
        ObservationWait.decode(cast(Never, bytearray(encoded)))
    with pytest.raises(ObserveIdentityError, match="size limit"):
        ObservationWait.decode(b"x" * (ObservationWait.MAX_ENCODED_BYTES + 1))
    oversized = ObservationWait("stream", ObservationCursor("stream", 10**4049), 1, "delivery")
    with pytest.raises(ObserveIdentityError, match="encoding exceeds"):
        oversized.encode()


def _encode_fields(fields: tuple[str, ...]) -> bytes:
    return b"".join(f"{len(field.encode('utf-8'))}:{field}".encode() for field in fields)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"x:mote.observe.wait.v1", "non-numeric"),
        (_encode_fields(("mote.observe.wait.v1", "stream", "01", "1", "delivery")), "cursor sequence"),
        (_encode_fields(("mote.observe.wait.v1", "stream", "0", "01", "delivery")), "observation revision"),
        (_encode_fields(("wrong.marker", "stream", "0", "1", "delivery")), "unknown codec marker"),
        (_encode_fields(("mote.observe.wait.v1", "stream", "-1", "1", "delivery")), "cursor sequence"),
        (_encode_fields(("mote.observe.wait.v1", "stream", "0", "-1", "delivery")), "observation revision"),
    ],
)
def test_wait_codec_rejects_noncanonical_or_corrupt_wire_values(payload: bytes, message: str) -> None:
    with pytest.raises(ObserveIdentityError, match=message):
        ObservationWait.decode(payload)


def test_wait_codec_rejects_trailing_bytes_and_invalid_marker() -> None:
    wait = ObservationWait("stream", _cursor(), 1, "delivery")
    with pytest.raises(ObserveIdentityError, match="trailing"):
        ObservationWait.decode(wait.encode() + b"x")
    fields = ("wrong.marker", "stream", "0", "1", "delivery")
    payload = _encode_fields(fields)
    with pytest.raises(ObserveIdentityError, match="unknown codec marker"):
        ObservationWait.decode(payload)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"020:mote.observe.wait.v1", "non-canonical length prefix"),
        (b"20:mote.observe.wait.v199:x", "truncated"),
        (b"20:mote.observe.wait.v11:\xff1:01:01:x", "valid UTF-8"),
    ],
)
def test_wait_codec_rejects_noncanonical_lengths_and_invalid_field_bytes(payload: bytes, message: str) -> None:
    with pytest.raises(ObserveIdentityError, match=message):
        ObservationWait.decode(payload)


def test_wait_codec_rejects_noncanonical_integer_fields() -> None:
    def encode_fields(sequence: str, revision: str) -> bytes:
        fields = ("mote.observe.wait.v1", "stream", sequence, revision, "delivery")
        return _encode_fields(fields)

    for payload, message in (
        (encode_fields("", "1"), "cursor sequence"),
        (encode_fields("01", "1"), "cursor sequence"),
        (encode_fields("0", ""), "observation revision"),
        (encode_fields("0", "01"), "observation revision"),
    ):
        with pytest.raises(ObserveIdentityError, match=message):
            ObservationWait.decode(payload)


def test_identity_records_are_slot_based() -> None:
    values = (
        DeliveryId("delivery"),
        _cursor(),
        _boundary(),
        CursorRange(_cursor(), _cursor(1)),
        BlockingTaskRef("task", 1, "wait"),
        ObservationWait("stream", _cursor(), 1, "delivery"),
        WaitRegistration("wait", "stream", _cursor(), 1),
        DeliveryAckReference("stream", (DeliveryId("delivery"),), "settlement"),
    )
    for value in values:
        assert "__dict__" not in type(value).__slots__

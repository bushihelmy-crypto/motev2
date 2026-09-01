"""Immutable usage, timing, error, and span lifecycle observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.observability.span import (
    ObservationContractError,
    Span,
    SpanContext,
    SpanStatus,
    require_observation_label,
)


class ObservationRecord:
    """Nominal base for values accepted by :class:`ObservabilityPort`."""

    __slots__ = ()


def _require_span(value: SpanContext | None, field: str) -> None:
    if value is not None and type(value) is not SpanContext:
        raise ObservationContractError(f"{field} must be a SpanContext or None")


def _require_duration(value: int, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ObservationContractError(f"{field} must be a non-negative integer nanosecond duration")


@dataclass(frozen=True, slots=True)
class ObservationError:
    """Safe, backend-neutral error facts; no exception object crosses the port."""

    category: str
    message: str | None = None
    handled: bool = False

    def __post_init__(self) -> None:
        require_observation_label(self.category, "error category")
        if self.message is not None and (
            type(self.message) is not str
            or not self.message
            or self.message != self.message.strip()
            or "\n" in self.message
            or "\r" in self.message
            or len(self.message) > 4_096
        ):
            raise ObservationContractError("error message must be a bounded, trimmed, single-line string when present")
        if type(self.handled) is not bool:
            raise ObservationContractError("error handled flag must be bool")


@dataclass(frozen=True, slots=True)
class UsageMeasurement:
    """One provider-neutral quantity, such as tokens, bytes, or requests."""

    name: str
    value: int | float
    unit: str

    def __post_init__(self) -> None:
        require_observation_label(self.name, "usage name")
        if type(self.value) not in (int, float) or isinstance(self.value, bool) or self.value < 0:
            raise ObservationContractError("usage value must be a non-negative number")
        if type(self.value) is float and not math.isfinite(self.value):
            raise ObservationContractError("usage value must be finite")
        require_observation_label(self.unit, "usage unit")


@dataclass(frozen=True, slots=True)
class UsageRecord(ObservationRecord):
    """One or more usage measurements associated with an optional span."""

    span: SpanContext | None
    measurements: tuple[UsageMeasurement, ...]

    def __post_init__(self) -> None:
        _require_span(self.span, "usage span")
        if type(self.measurements) is not tuple or not self.measurements:
            raise ObservationContractError("usage measurements must be a non-empty tuple")
        if any(type(measurement) is not UsageMeasurement for measurement in self.measurements):
            raise ObservationContractError("usage measurements must contain only UsageMeasurement values")
        names = tuple(measurement.name for measurement in self.measurements)
        if len(names) != len(set(names)):
            raise ObservationContractError("usage measurement names must be unique")


@dataclass(frozen=True, slots=True)
class TimingRecord(ObservationRecord):
    """One elapsed duration associated with an optional span."""

    span: SpanContext | None
    name: str
    duration_ns: int

    def __post_init__(self) -> None:
        _require_span(self.span, "timing span")
        require_observation_label(self.name, "timing name")
        _require_duration(self.duration_ns, "timing duration")


@dataclass(frozen=True, slots=True)
class ErrorRecord(ObservationRecord):
    """One normalized error observation associated with an optional span."""

    span: SpanContext | None
    error: ObservationError

    def __post_init__(self) -> None:
        _require_span(self.span, "error span")
        if type(self.error) is not ObservationError:
            raise ObservationContractError("error record must contain an ObservationError")


@dataclass(frozen=True, slots=True)
class SpanStarted(ObservationRecord):
    """A span became active."""

    span: Span

    def __post_init__(self) -> None:
        if type(self.span) is not Span:
            raise ObservationContractError("span started record must contain a Span")


@dataclass(frozen=True, slots=True)
class SpanFinished(ObservationRecord):
    """A span ended with a status and elapsed duration."""

    span: SpanContext
    status: SpanStatus
    duration_ns: int
    error: ObservationError | None = None

    def __post_init__(self) -> None:
        if type(self.span) is not SpanContext:
            raise ObservationContractError("span finished record must contain a SpanContext")
        if type(self.status) is not SpanStatus:
            raise ObservationContractError("span finished status must be a SpanStatus")
        _require_duration(self.duration_ns, "span finished duration")
        if self.error is not None and type(self.error) is not ObservationError:
            raise ObservationContractError("span finished error must be an ObservationError or None")
        if self.status is SpanStatus.ERROR and self.error is None:
            raise ObservationContractError("an error span must carry normalized error facts")
        if self.status is not SpanStatus.ERROR and self.error is not None:
            raise ObservationContractError("only an error span may carry normalized error facts")


Observation: TypeAlias = SpanStarted | SpanFinished | UsageRecord | TimingRecord | ErrorRecord


__all__ = [
    "ErrorRecord",
    "Observation",
    "ObservationError",
    "ObservationRecord",
    "SpanFinished",
    "SpanStarted",
    "TimingRecord",
    "UsageMeasurement",
    "UsageRecord",
]

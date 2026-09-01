"""Opaque trace/span identity and immutable parent-child span values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import NewType, TypeAlias

TraceId = NewType("TraceId", str)
SpanId = NewType("SpanId", str)
ObservationValue: TypeAlias = str | int | float | bool | None


class ObservationContractError(ValueError):
    """Raised when an observation value crosses its typed boundary incorrectly."""


def require_observation_label(value: str, field: str) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or len(value) > 256
    ):
        raise ObservationContractError(f"{field} must be a short, trimmed, single-line value")


def _require_attribute_value(value: ObservationValue, field: str) -> None:
    if value is not None and type(value) not in (str, int, float, bool):
        raise ObservationContractError(f"{field} must be a scalar observation value")
    if type(value) is float and not math.isfinite(value):
        raise ObservationContractError(f"{field} float must be finite")
    if type(value) is str and ("\n" in value or "\r" in value or len(value) > 4_096):
        raise ObservationContractError(f"{field} string must be bounded and single-line")


class SpanKind(StrEnum):
    """Provider-neutral role of one span in a trace."""

    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


class SpanStatus(StrEnum):
    """Lifecycle status reported when a span is finished."""

    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ObservationAttribute:
    """One scalar attribute attached to a span."""

    name: str
    value: ObservationValue

    def __post_init__(self) -> None:
        require_observation_label(self.name, "observation attribute name")
        _require_attribute_value(self.value, "observation attribute value")


@dataclass(frozen=True, slots=True)
class SpanContext:
    """Opaque identity for one span and its parent within one trace."""

    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None = None

    def __post_init__(self) -> None:
        require_observation_label(self.trace_id, "trace id")
        require_observation_label(self.span_id, "span id")
        if self.parent_span_id is not None:
            require_observation_label(self.parent_span_id, "parent span id")
            if self.parent_span_id == self.span_id:
                raise ObservationContractError("a span cannot be its own parent")


@dataclass(frozen=True, slots=True)
class Span:
    """Immutable span start description consumed by an observation adapter."""

    context: SpanContext
    name: str
    kind: SpanKind = SpanKind.INTERNAL
    attributes: tuple[ObservationAttribute, ...] = ()

    def __post_init__(self) -> None:
        if type(self.context) is not SpanContext:
            raise ObservationContractError("span context must be a SpanContext")
        require_observation_label(self.name, "span name")
        if type(self.kind) is not SpanKind:
            raise ObservationContractError("span kind must be a SpanKind")
        if type(self.attributes) is not tuple:
            raise ObservationContractError("span attributes must be a tuple")
        if any(type(attribute) is not ObservationAttribute for attribute in self.attributes):
            raise ObservationContractError("span attributes must contain only ObservationAttribute values")
        names = tuple(attribute.name for attribute in self.attributes)
        if len(names) != len(set(names)):
            raise ObservationContractError("span attribute names must be unique")


__all__ = [
    "ObservationAttribute",
    "ObservationContractError",
    "ObservationValue",
    "Span",
    "SpanContext",
    "SpanId",
    "SpanKind",
    "SpanStatus",
    "TraceId",
]

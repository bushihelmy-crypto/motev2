"""Immutable, transport-independent structured diagnostic values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.logging.level import LogLevel

LogValue: TypeAlias = str | int | float | bool | None


class LogContractError(ValueError):
    """Raised when a value does not satisfy the logging contract."""


def require_log_label(value: str, field: str) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or len(value) > 128
    ):
        raise LogContractError(f"{field} must be a short, trimmed, single-line value")


def _require_value(value: LogValue, field: str) -> None:
    if value is not None and type(value) not in (str, int, float, bool):
        raise LogContractError(f"{field} must be a scalar logging value")
    if type(value) is float and not math.isfinite(value):
        raise LogContractError(f"{field} float must be finite")
    if type(value) is str and ("\n" in value or "\r" in value or len(value) > 4_096):
        raise LogContractError(f"{field} string must be bounded and single-line")


@dataclass(frozen=True, slots=True)
class LogField:
    """One named scalar field in a structured log record."""

    name: str
    value: LogValue

    def __post_init__(self) -> None:
        require_log_label(self.name, "log field name")
        _require_value(self.value, "log field value")


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One immutable diagnostic record.

    Timestamps, formatting, filtering, and transport metadata belong to the
    sink or its surrounding runtime.  Callers put correlation values such as
    ``run_id`` or ``trace_id`` in the typed field sequence.
    """

    level: LogLevel
    event: str
    message: str | None = None
    fields: tuple[LogField, ...] = ()

    def __post_init__(self) -> None:
        if type(self.level) is not LogLevel:
            raise LogContractError("log level must be a LogLevel")
        require_log_label(self.event, "log event")
        if self.message is not None and (
            type(self.message) is not str
            or not self.message
            or self.message != self.message.strip()
            or "\n" in self.message
            or "\r" in self.message
            or len(self.message) > 4_096
        ):
            raise LogContractError("log message must be a bounded, trimmed, single-line string when present")
        if type(self.fields) is not tuple:
            raise LogContractError("log fields must be a tuple")
        if any(type(field) is not LogField for field in self.fields):
            raise LogContractError("log fields must contain only LogField values")
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise LogContractError("log field names must be unique")


__all__ = ["LogContractError", "LogField", "LogRecord", "LogValue"]

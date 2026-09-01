"""Optional diagnostic logging around one asynchronous node invocation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Generic, Protocol, TypeAlias, TypeVar

from mote_kernel.logging.level import LogLevel
from mote_kernel.logging.port import LogSinkPort
from mote_kernel.logging.record import LogContractError, LogField, LogRecord, require_log_label

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
NodeOperation: TypeAlias = Callable[[InputT], Awaitable[OutputT]]
_LIFECYCLE_FIELD_NAMES = frozenset(("duration_ns", "error_type", "outcome"))


class NodeLogFields(Protocol):
    """Produce invocation-safe fields such as a run or scope coordinate."""

    def __call__(self, /) -> tuple[LogField, ...]: ...


def _merge_fields(
    fixed: tuple[LogField, ...],
    dynamic: tuple[LogField, ...],
    extra: tuple[LogField, ...] = (),
) -> tuple[LogField, ...]:
    if type(dynamic) is not tuple or any(type(field) is not LogField for field in dynamic):
        raise LogContractError("node log fields factory must return a tuple of LogField values")
    fields = (*fixed, *dynamic, *extra)
    names = tuple(field.name for field in fields)
    if len(names) != len(set(names)):
        raise LogContractError("node log field names must be unique")
    return fields


@dataclass(frozen=True, slots=True)
class LoggedNode(Generic[InputT, OutputT]):
    """Wrap one node callable with best-effort lifecycle diagnostics.

    The wrapper never changes the callable's result or exception.  Diagnostic
    construction and sink failures are deliberately ignored because logging
    is a side-channel, not part of graph state or settlement semantics.
    """

    inner: NodeOperation[InputT, OutputT]
    sink: LogSinkPort
    event: str = "node"
    fields: tuple[LogField, ...] = ()
    fields_factory: NodeLogFields | None = None

    def __post_init__(self) -> None:
        require_log_label(self.event, "node log event")
        require_log_label(f"{self.event}.cancelled", "node log event")
        if type(self.fields) is not tuple or any(type(field) is not LogField for field in self.fields):
            raise LogContractError("node log fields must be a tuple of LogField values")
        LogRecord(LogLevel.DEBUG, f"{self.event}.started", fields=self.fields)
        if any(field.name in _LIFECYCLE_FIELD_NAMES for field in self.fields):
            raise LogContractError("node log fixed fields cannot use lifecycle field names")

    def _invocation_fields(self) -> tuple[LogField, ...]:
        try:
            dynamic = () if self.fields_factory is None else self.fields_factory()
            fields = _merge_fields(self.fields, dynamic)
            if any(field.name in _LIFECYCLE_FIELD_NAMES for field in dynamic):
                raise LogContractError("node log dynamic fields cannot use lifecycle field names")
            return fields
        except (asyncio.CancelledError, Exception):
            return self.fields

    def _write(
        self,
        level: LogLevel,
        event: str,
        fields: tuple[LogField, ...],
        *,
        error: BaseException | None = None,
    ) -> None:
        try:
            diagnostic_fields = fields if error is None else (*fields, LogField("error_type", type(error).__name__))
            self.sink.write(LogRecord(level, event, fields=diagnostic_fields))
        except (asyncio.CancelledError, Exception):
            return

    async def __call__(self, value: InputT, /) -> OutputT:
        fields = self._invocation_fields()
        self._write(LogLevel.DEBUG, f"{self.event}.started", fields)
        started = perf_counter_ns()
        try:
            result = await self.inner(value)
        except asyncio.CancelledError as error:
            self._write(
                LogLevel.WARNING,
                f"{self.event}.cancelled",
                _merge_fields(
                    fields,
                    (),
                    (
                        LogField("outcome", "cancelled"),
                        LogField("duration_ns", perf_counter_ns() - started),
                    ),
                ),
                error=error,
            )
            raise
        except Exception as error:
            self._write(
                LogLevel.ERROR,
                f"{self.event}.failed",
                _merge_fields(
                    fields,
                    (),
                    (
                        LogField("outcome", "error"),
                        LogField("duration_ns", perf_counter_ns() - started),
                    ),
                ),
                error=error,
            )
            raise
        self._write(
            LogLevel.INFO,
            f"{self.event}.finished",
            _merge_fields(
                fields,
                (),
                (
                    LogField("outcome", "ok"),
                    LogField("duration_ns", perf_counter_ns() - started),
                ),
            ),
        )
        return result


__all__ = ["LoggedNode"]

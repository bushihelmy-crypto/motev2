"""Optional diagnostic logging around one asynchronous node invocation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Generic, Protocol, TypeAlias, TypeVar

from mote_kernel.logging.emit import write_diagnostic
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
    if any(field.name in _LIFECYCLE_FIELD_NAMES for field in dynamic):
        raise LogContractError("node log dynamic fields cannot use lifecycle field names")
    fields = (*fixed, *dynamic, *extra)
    names = tuple(field.name for field in fields)
    if len(names) != len(set(names)):
        raise LogContractError("node log field names must be unique")
    return fields


@dataclass(frozen=True, slots=True)
class LoggedNode:
    """Configure best-effort lifecycle diagnostics for one node callable."""

    sink: LogSinkPort
    event: str = field(default="node", kw_only=True)
    fields: tuple[LogField, ...] = field(default=(), kw_only=True)
    fields_factory: NodeLogFields | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        require_log_label(self.event, "node log event")
        require_log_label(f"{self.event}.cancelled", "node log event")
        if type(self.fields) is not tuple or any(type(field) is not LogField for field in self.fields):
            raise LogContractError("node log fields must be a tuple of LogField values")
        LogRecord(LogLevel.DEBUG, f"{self.event}.started", fields=self.fields)
        if any(field.name in _LIFECYCLE_FIELD_NAMES for field in self.fields):
            raise LogContractError("node log fixed fields cannot use lifecycle field names")

    def __call__(
        self,
        inner: NodeOperation[InputT, OutputT],
    ) -> NodeOperation[InputT, OutputT]:
        return _LoggedNode(inner, self)


def _invocation_fields(config: LoggedNode) -> tuple[LogField, ...]:
    factory = config.fields_factory
    if factory is None:
        return config.fields
    try:
        dynamic = factory()
    except Exception:
        return config.fields
    try:
        return _merge_fields(config.fields, dynamic)
    except LogContractError:
        return config.fields


@dataclass(frozen=True, slots=True)
class _LoggedNode(Generic[InputT, OutputT]):
    """Apply one immutable logging configuration to a typed node callable."""

    inner: NodeOperation[InputT, OutputT]
    config: LoggedNode

    async def __call__(self, value: InputT, /) -> OutputT:
        fields = _invocation_fields(self.config)
        await write_diagnostic(self.config.sink, LogLevel.DEBUG, f"{self.config.event}.started", fields)
        started = perf_counter_ns()
        try:
            result = await self.inner(value)
        except asyncio.CancelledError as error:
            with suppress(asyncio.CancelledError):
                await write_diagnostic(
                    self.config.sink,
                    LogLevel.WARNING,
                    f"{self.config.event}.cancelled",
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
            with suppress(asyncio.CancelledError):
                await write_diagnostic(
                    self.config.sink,
                    LogLevel.ERROR,
                    f"{self.config.event}.failed",
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
        with suppress(asyncio.CancelledError):
            await write_diagnostic(
                self.config.sink,
                LogLevel.INFO,
                f"{self.config.event}.finished",
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

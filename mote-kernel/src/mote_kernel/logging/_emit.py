"""Internal best-effort delivery of structured log records."""

from __future__ import annotations

import asyncio

from mote_kernel.logging.level import LogLevel
from mote_kernel.logging.port import LogSinkPort
from mote_kernel.logging.record import LogField, LogRecord


def write_best_effort(
    sink: LogSinkPort,
    level: LogLevel,
    event: str,
    fields: tuple[LogField, ...],
    *,
    error: Exception | asyncio.CancelledError | None = None,
) -> None:
    """Deliver one diagnostic without changing the wrapped operation."""

    try:
        diagnostic_fields = fields if error is None else (*fields, LogField("error_type", type(error).__name__))
        sink.write(LogRecord(level, event, fields=diagnostic_fields))
    except asyncio.CancelledError:
        return
    except Exception:
        return

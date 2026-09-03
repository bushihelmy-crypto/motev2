"""Internal delivery of structured log records through the configured port."""

from __future__ import annotations

import asyncio

from mote_kernel.logging.level import LogLevel
from mote_kernel.logging.port import LogSinkPort
from mote_kernel.logging.record import LogContractError, LogField, LogRecord


async def write_diagnostic(
    sink: LogSinkPort,
    level: LogLevel,
    event: str,
    fields: tuple[LogField, ...],
    *,
    error: Exception | asyncio.CancelledError | None = None,
) -> None:
    """Build one diagnostic and await its best-effort Port delivery."""

    try:
        diagnostic_fields = fields if error is None else (*fields, LogField("error_type", type(error).__name__))
        record = LogRecord(level, event, fields=diagnostic_fields)
    except LogContractError:
        return
    await sink.write(record)

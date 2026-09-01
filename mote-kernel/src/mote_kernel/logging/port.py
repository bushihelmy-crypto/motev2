"""The narrow output capability for structured diagnostic records."""

from typing import Protocol

from mote_kernel.logging.record import LogRecord


class LogSinkPort(Protocol):
    """Accept one Kernel log record without choosing a destination backend.

    A concrete sink may enqueue, format, filter, persist, or forward the
    record.  None of those policies are part of the Kernel contract.
    """

    def write(self, record: LogRecord, /) -> None: ...


__all__ = ["LogSinkPort"]

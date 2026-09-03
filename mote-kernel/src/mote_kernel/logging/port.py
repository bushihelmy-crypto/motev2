"""The Kernel-side invocation adapter for structured diagnostic records."""

from dataclasses import dataclass, field

from mote_kernel.invocation import BEST_EFFORT_TIMEOUT_SECONDS, Invocation, invoke_best_effort
from mote_kernel.logging.record import LogRecord


@dataclass(frozen=True, slots=True)
class LogSinkPort:
    """Adapt one best-effort invocation to the Kernel logging vocabulary.

    Resolution of the invocation (local, RPC, or another configured
    implementation) belongs outside the Kernel; this adapter fixes the
    diagnostic error policy and preserves the typed ``LogRecord`` request at
    the domain boundary.
    """

    invocation: Invocation[LogRecord, None]
    timeout_seconds: float = field(default=BEST_EFFORT_TIMEOUT_SECONDS, kw_only=True)

    async def write(self, record: LogRecord, /) -> None:
        """Forward one record exactly once on the best-effort invocation path."""

        await invoke_best_effort(self.invocation, record, timeout_seconds=self.timeout_seconds)


__all__ = ["LogSinkPort"]

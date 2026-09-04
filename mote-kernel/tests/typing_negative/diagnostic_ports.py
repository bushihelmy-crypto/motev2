"""Well-typed diagnostic ports used to isolate negative fixture errors."""

from mote_kernel.logging.port import LogSinkPort
from mote_kernel.logging.record import LogRecord
from mote_kernel.observability.port import ObservabilityPort
from mote_kernel.observability.record import Observation


class _LogInvocation:
    async def invoke(self, _record: LogRecord, /) -> None:
        pass


class _ObservationInvocation:
    async def invoke(self, _observation: Observation, /) -> None:
        pass


LOG_SINK = LogSinkPort(_LogInvocation())
OBSERVABILITY_PORT = ObservabilityPort(_ObservationInvocation())

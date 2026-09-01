"""The backend-neutral observation output capability."""

from typing import Protocol

from mote_kernel.observability.record import Observation


class ObservabilityPort(Protocol):
    """Accept normalized observations for a runtime-selected adapter.

    The implementation may map records to OpenTelemetry, Langfuse, a local
    buffer, or another system.  None of those choices are visible to Kernel
    code.  Implementations must return promptly; asynchronous export and
    buffering remain adapter concerns so observation adds no execution await
    point.
    """

    def record(self, observation: Observation, /) -> None: ...


__all__ = ["ObservabilityPort"]

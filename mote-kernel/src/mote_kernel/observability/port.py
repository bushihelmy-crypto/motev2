"""The Kernel-side invocation adapter for normalized observations."""

from dataclasses import dataclass, field

from mote_kernel.invocation import BEST_EFFORT_TIMEOUT_SECONDS, Invocation, invoke_best_effort
from mote_kernel.observability.record import Observation


@dataclass(frozen=True, slots=True)
class ObservabilityPort:
    """Adapt one best-effort invocation to the Kernel observation vocabulary.

    Transport and implementation selection are performed by the invocation
    infrastructure; this adapter fixes the diagnostic error policy and owns
    only the typed observation request.
    """

    invocation: Invocation[Observation, None]
    timeout_seconds: float = field(default=BEST_EFFORT_TIMEOUT_SECONDS, kw_only=True)

    async def record(self, observation: Observation, /) -> None:
        """Forward one observation exactly once on the best-effort path."""

        await invoke_best_effort(self.invocation, observation, timeout_seconds=self.timeout_seconds)


__all__ = ["ObservabilityPort"]

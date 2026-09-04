"""The Kernel-side invocation adapter for settlement event references."""

from dataclasses import dataclass, field

from mote_kernel.events.record import NodeSettlementEventReference
from mote_kernel.invocation import BEST_EFFORT_TIMEOUT_SECONDS, Invocation, invoke_best_effort


@dataclass(frozen=True, slots=True)
class EventPort:
    """Adapt one configured invocation to the best-effort Events boundary.

    The invocation implementation and its transport are selected by
    composition.  Events only owns the typed settlement reference and the
    diagnostic error policy; it does not persist, retry, or dispatch events.
    Callers should invoke this port only after the authoritative commit has
    completed when they use it as a notification of that commit.
    """

    invocation: Invocation[NodeSettlementEventReference, None]
    timeout_seconds: float = field(default=BEST_EFFORT_TIMEOUT_SECONDS, kw_only=True)

    async def emit(self, event: NodeSettlementEventReference, /) -> None:
        """Forward one settlement reference exactly once, best effort."""

        await invoke_best_effort(self.invocation, event, timeout_seconds=self.timeout_seconds)


__all__ = ["EventPort"]

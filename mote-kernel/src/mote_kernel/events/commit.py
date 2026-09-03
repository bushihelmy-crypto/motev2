"""Atomic outbox decoration for the graph commit boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.events.port import EventPort
from mote_kernel.events.projection import project_event
from mote_kernel.events.record import NodeSettlementEventReference
from mote_kernel.execution import Graph

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class AtomicCommitRequest(Generic[GraphValueT]):
    """The complete write set offered to one persistence transaction."""

    transition: Graph.Transition[GraphValueT]
    event_reference: NodeSettlementEventReference | None


AtomicPersistenceCommit: TypeAlias = Callable[
    [AtomicCommitRequest[GraphValueT]],
    Awaitable[Graph.State],
]


@dataclass(frozen=True, slots=True)
class EventingGraphCommit(Generic[GraphValueT]):
    """Wrap one atomic persistence port as an ordinary ``Graph.Commit``.

    The persistence owner commits the transition and optional outbox reference
    together.  An optional ``EventPort`` receives the already-confirmed
    settlement reference after that transaction; it is a best-effort
    notification and never becomes a second commit or state owner.
    """

    persistence: AtomicPersistenceCommit[GraphValueT]
    event_port: EventPort | None = None

    async def __call__(
        self,
        transition: Graph.Transition[GraphValueT],
        /,
    ) -> Graph.State:
        request = AtomicCommitRequest(
            transition=transition,
            event_reference=project_event(transition),
        )
        confirmed = await self.persistence(request)
        if (
            self.event_port is not None
            and request.event_reference is not None
            and type(confirmed) is Graph.State
            and confirmed == transition.candidate_state
        ):
            await self.event_port.emit(request.event_reference)
        return confirmed


__all__ = ["EventingGraphCommit"]

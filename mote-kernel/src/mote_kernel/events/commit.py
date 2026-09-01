"""Atomic outbox decoration for the graph commit boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

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
    together. Remote delivery happens after this boundary and is never invoked
    by this decorator.
    """

    persistence: AtomicPersistenceCommit[GraphValueT]

    async def __call__(
        self,
        transition: Graph.Transition[GraphValueT],
        /,
    ) -> Graph.State:
        request = AtomicCommitRequest(
            transition=transition,
            event_reference=project_event(transition),
        )
        return await self.persistence(request)


__all__ = ["EventingGraphCommit"]

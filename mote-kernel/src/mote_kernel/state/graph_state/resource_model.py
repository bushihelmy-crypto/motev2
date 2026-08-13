"""Immutable snapshots for ordered exclusive-resource acquisition."""

from dataclasses import dataclass
from typing import NewType

from mote_kernel.state.graph_state.identity import GraphNodeId

ResourceId = NewType("ResourceId", str)


@dataclass(frozen=True, slots=True)
class ResourceLock:
    resource_id: ResourceId
    owner: GraphNodeId | None = None
    waiters: tuple[GraphNodeId, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceAcquisition:
    node_id: GraphNodeId
    required: tuple[ResourceId, ...]
    acquired: tuple[ResourceId, ...]
    waiting_for: ResourceId | None = None

    @property
    def admitted(self) -> bool:
        return self.acquired == self.required and self.waiting_for is None


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    resources: tuple[ResourceLock, ...]
    acquisitions: tuple[ResourceAcquisition, ...] = ()


__all__ = ["ResourceAcquisition", "ResourceId", "ResourceLock", "ResourceSnapshot"]

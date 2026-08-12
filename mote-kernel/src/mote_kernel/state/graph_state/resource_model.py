"""Immutable snapshots for ordered exclusive-resource acquisition."""

from dataclasses import dataclass
from typing import NewType

ResourceId = NewType("ResourceId", str)

ParticipantId = NewType("ParticipantId", str)


@dataclass(frozen=True, slots=True)
class ResourceLock:
    """Current owner and FIFO waiters for one exclusive resource."""

    resource_id: ResourceId
    owner: ParticipantId | None = None
    waiters: tuple[ParticipantId, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceAcquisition:
    """One participant's ordered resource-acquisition progress."""

    participant_id: ParticipantId
    required: tuple[ResourceId, ...]
    acquired: tuple[ResourceId, ...]
    waiting_for: ResourceId | None = None

    @property
    def admitted(self) -> bool:
        """Return whether every required resource has been acquired."""

        return self.acquired == self.required and self.waiting_for is None


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Read-only lock ownership and acquisition progress."""

    resources: tuple[ResourceLock, ...]
    acquisitions: tuple[ResourceAcquisition, ...] = ()


__all__ = ["ParticipantId", "ResourceAcquisition", "ResourceId", "ResourceLock", "ResourceSnapshot"]

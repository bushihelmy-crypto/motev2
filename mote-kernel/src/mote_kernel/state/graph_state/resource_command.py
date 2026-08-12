"""Typed commands for ordered exclusive-resource acquisition."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.state.graph_state.resource_model import ParticipantId, ResourceId


@dataclass(frozen=True, slots=True)
class AcquireResources:
    """Begin acquiring a fixed resource set in snapshot order."""

    participant_id: ParticipantId
    resources: tuple[ResourceId, ...]


@dataclass(frozen=True, slots=True)
class ReleaseResources:
    """Release every resource held by one admitted participant."""

    participant_id: ParticipantId


ResourceCommand: TypeAlias = AcquireResources | ReleaseResources

__all__ = ["AcquireResources", "ReleaseResources", "ResourceCommand"]

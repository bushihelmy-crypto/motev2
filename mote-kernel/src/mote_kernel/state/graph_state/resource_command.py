"""Typed commands for ordered exclusive-resource acquisition."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.state.graph_state.identity import GraphNodeId
from mote_kernel.state.graph_state.resource_model import ResourceId


@dataclass(frozen=True, slots=True)
class AcquireResources:
    node_id: GraphNodeId
    resources: tuple[ResourceId, ...]


@dataclass(frozen=True, slots=True)
class ReleaseResources:
    node_id: GraphNodeId


ResourceCommand: TypeAlias = AcquireResources | ReleaseResources

__all__ = ["AcquireResources", "ReleaseResources", "ResourceCommand"]

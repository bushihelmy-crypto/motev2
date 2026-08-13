"""Static graph edge definitions."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.state.graph_state.identity import GraphNodeId, GraphRouteId


@dataclass(frozen=True, slots=True)
class DirectEdge:
    source: GraphNodeId
    target: GraphNodeId


@dataclass(frozen=True, slots=True)
class ConditionalEdge:
    source: GraphNodeId
    route: GraphRouteId
    target: GraphNodeId


@dataclass(frozen=True, slots=True)
class JoinEdge:
    sources: tuple[GraphNodeId, ...]
    target: GraphNodeId


Edge: TypeAlias = DirectEdge | ConditionalEdge | JoinEdge

__all__ = ["ConditionalEdge", "DirectEdge", "Edge", "JoinEdge"]

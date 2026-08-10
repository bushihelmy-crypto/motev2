"""Static graph edge definitions."""

from dataclasses import dataclass
from typing import NewType, TypeAlias

from mote_kernel.execution.graph.node import NodeId

RouteId = NewType("RouteId", str)


@dataclass(frozen=True, slots=True)
class DirectEdge:
    """Advance from one completed node to one target node."""

    source: NodeId
    target: NodeId


@dataclass(frozen=True, slots=True)
class ConditionalEdge:
    """Declare one valid route from a source under a stable route identity."""

    source: NodeId
    route: RouteId
    target: NodeId


@dataclass(frozen=True, slots=True)
class JoinEdge:
    """Advance only after every source in the join has completed."""

    sources: tuple[NodeId, ...]
    target: NodeId


Edge: TypeAlias = DirectEdge | ConditionalEdge | JoinEdge

__all__ = ["ConditionalEdge", "DirectEdge", "Edge", "JoinEdge", "RouteId"]

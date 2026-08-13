"""State-owned graph routing contributions."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.state.graph_state.identity import GraphRouteId


@dataclass(frozen=True, slots=True)
class ContinueGraphRouting:
    """Follow the static direct edges of a non-conditional node."""


@dataclass(frozen=True, slots=True)
class SelectGraphRoute:
    """Select one stable route declared by a conditional node."""

    route: GraphRouteId


GraphRoutingContribution: TypeAlias = ContinueGraphRouting | SelectGraphRoute

__all__ = ["ContinueGraphRouting", "GraphRoutingContribution", "SelectGraphRoute"]

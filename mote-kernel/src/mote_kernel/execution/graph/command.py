"""Typed graph routing commands emitted by nodes."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.execution.graph.edge import RouteId


@dataclass(frozen=True, slots=True)
class Continue:
    """Follow every static direct edge without selecting an optional route."""


@dataclass(frozen=True, slots=True)
class SelectRoute:
    """Select exactly one declared conditional route from the completed node."""

    route: RouteId


RoutingCommand: TypeAlias = Continue | SelectRoute

__all__ = ["Continue", "RoutingCommand", "SelectRoute"]

"""Typed outcomes of exactly one graph-node invocation."""

from dataclasses import dataclass, field
from typing import Generic, Never, TypeVar

from mote_kernel.execution.graph.command import Continue, RoutingCommand

OutputT_co = TypeVar("OutputT_co", covariant=True)


class NodeOutcome(Generic[OutputT_co]):
    """Open result boundary for one graph-node invocation."""


@dataclass(frozen=True, slots=True)
class NodeSuccess(NodeOutcome[OutputT_co], Generic[OutputT_co]):
    """One successful node invocation and its routing decision."""

    output: OutputT_co
    routing: RoutingCommand = field(default_factory=Continue)


@dataclass(frozen=True, slots=True)
class NodeFailure(NodeOutcome[Never]):
    """One final node-level failure after capability-local failover."""

    failure: str


__all__ = ["NodeFailure", "NodeOutcome", "NodeSuccess"]

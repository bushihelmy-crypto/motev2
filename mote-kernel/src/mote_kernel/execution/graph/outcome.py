"""Typed outcomes of exactly one graph-node invocation."""

from dataclasses import dataclass, field
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.state.graph_state.frontier_model import GraphFailure, GraphInterruptPayload
from mote_kernel.state.graph_state.routing import ContinueGraphRouting, GraphRoutingContribution

OutputT_co = TypeVar("OutputT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class NodeSuccess(Generic[OutputT_co]):
    output: OutputT_co
    routing: GraphRoutingContribution = field(default_factory=ContinueGraphRouting)


@dataclass(frozen=True, slots=True)
class NodeFailure:
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class NodeInterrupt:
    request_payload: GraphInterruptPayload


NodeOutcome: TypeAlias = NodeSuccess[OutputT_co] | NodeFailure | NodeInterrupt

__all__ = ["NodeFailure", "NodeInterrupt", "NodeOutcome", "NodeSuccess"]

"""Graph node contracts."""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.execution.graph.identity import NodeId
from mote_kernel.execution.graph.outcome import NodeOutcome

InputT = TypeVar("InputT", contravariant=True)
OutputT_co = TypeVar("OutputT_co", covariant=True)


class Node(Protocol[InputT, OutputT_co]):
    """Execute one node invocation without graph-level retry."""

    def __call__(self, node_input: InputT) -> "NodeOutcome[OutputT_co]":
        """Return the typed outcome of exactly one node invocation."""
        ...


@dataclass(frozen=True, slots=True)
class NodeDefinition(Generic[InputT, OutputT_co]):
    """Bind a stable node identity to one executable node."""

    node_id: NodeId
    node: Node[InputT, OutputT_co]


__all__ = ["Node", "NodeDefinition", "NodeId"]

"""Graph node contracts."""

from dataclasses import dataclass
from typing import Generic, NewType, Protocol, TypeVar

NodeId = NewType("NodeId", str)

InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT", covariant=True)


class Node(Protocol[InputT, OutputT]):
    """Execute one node invocation without graph-level retry."""

    def __call__(self, node_input: InputT) -> OutputT:
        """Return the result of exactly one node invocation."""
        ...


@dataclass(frozen=True, slots=True)
class NodeDefinition(Generic[InputT, OutputT]):
    """Bind a stable node identity to one executable node."""

    node_id: NodeId
    node: Node[InputT, OutputT]


__all__ = ["Node", "NodeDefinition", "NodeId"]

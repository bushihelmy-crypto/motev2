"""Graph node contracts."""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.execution.graph.identity import NodeId
from mote_kernel.execution.graph.outcome import NodeOutcome
from mote_kernel.parallel import ResourceId

InputT = TypeVar("InputT", contravariant=True)
OutputT_co = TypeVar("OutputT_co", covariant=True)


class Node(Protocol[InputT, OutputT_co]):
    """Execute once against a shared immutable input snapshot without graph-level retry."""

    async def __call__(self, node_input: InputT) -> "NodeOutcome[OutputT_co]":
        """Return a typed outcome without mutating the shared input snapshot."""
        ...


@dataclass(frozen=True, slots=True)
class NodeDefinition(Generic[InputT, OutputT_co]):
    """Bind a stable node identity to one executable node."""

    node_id: NodeId
    node: Node[InputT, OutputT_co]
    resources: tuple[ResourceId, ...] = ()


__all__ = ["Node", "NodeDefinition", "NodeId"]

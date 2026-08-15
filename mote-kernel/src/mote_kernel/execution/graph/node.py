"""Graph node contracts."""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.execution.graph.outcome import NodeOutcome
from mote_kernel.execution.resource import ResourceId
from mote_kernel.state.graph_state.identity import GraphNodeId

InputT = TypeVar("InputT", contravariant=True)
OutputT_co = TypeVar("OutputT_co", covariant=True)


class Node(Protocol[InputT, OutputT_co]):
    async def __call__(self, node_input: InputT, /) -> NodeOutcome[OutputT_co]: ...


@dataclass(frozen=True, slots=True)
class NodeDefinition(Generic[InputT, OutputT_co]):
    node_id: GraphNodeId
    node: Node[InputT, OutputT_co]
    resources: tuple[ResourceId, ...] = ()


__all__ = ["Node", "NodeDefinition"]

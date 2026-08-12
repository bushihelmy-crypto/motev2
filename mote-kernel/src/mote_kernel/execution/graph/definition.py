"""Immutable, versioned graph definitions."""

from dataclasses import dataclass
from typing import Generic, NewType, TypeAlias, TypeVar

from mote_kernel.execution.graph.edge import Edge
from mote_kernel.execution.graph.node import NodeDefinition, NodeId
from mote_kernel.execution.graph.resolution import ResolutionBinding
from mote_kernel.execution.resource import ResourceDefinition

GraphDefinitionId = NewType("GraphDefinitionId", str)
GraphDefinitionVersion = NewType("GraphDefinitionVersion", int)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class NestedGraphNodeDefinition(Generic[InputT, OutputT]):
    """Bind a stable node identity to a graph using the same run semantics."""

    node_id: NodeId
    graph: "GraphDefinition[InputT, OutputT]"


GraphNode: TypeAlias = NodeDefinition[InputT, OutputT] | NestedGraphNodeDefinition[InputT, OutputT]


@dataclass(frozen=True, slots=True)
class GraphDefinition(Generic[InputT, OutputT]):
    """An immutable graph declaration independent of any graph run."""

    definition_id: GraphDefinitionId
    version: GraphDefinitionVersion
    nodes: tuple[GraphNode[InputT, OutputT], ...]
    edges: tuple[Edge, ...]
    entries: tuple[NodeId, ...]
    resources: tuple[ResourceDefinition, ...] = ()
    resolution: ResolutionBinding[InputT] | None = None


__all__ = [
    "GraphDefinition",
    "GraphDefinitionId",
    "GraphDefinitionVersion",
    "GraphNode",
    "NestedGraphNodeDefinition",
]

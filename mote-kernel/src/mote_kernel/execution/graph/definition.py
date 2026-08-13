"""Immutable, versioned graph definitions."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.graph.edge import Edge
from mote_kernel.execution.graph.node import NodeDefinition
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.state.graph_state.identity import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class NestedGraphNodeDefinition(Generic[InputT, OutputT]):
    node_id: GraphNodeId
    graph: "GraphDefinition[InputT, OutputT]"


GraphNode: TypeAlias = NodeDefinition[InputT, OutputT] | NestedGraphNodeDefinition[InputT, OutputT]


@dataclass(frozen=True, slots=True)
class GraphDefinition(Generic[InputT, OutputT]):
    definition_id: GraphDefinitionId
    version: GraphDefinitionVersion
    nodes: tuple[GraphNode[InputT, OutputT], ...]
    edges: tuple[Edge, ...]
    entries: tuple[GraphNodeId, ...]
    resources: tuple[ResourceDefinition, ...] = ()
    resume_input: ResumeInputBinding[InputT] | None = None


__all__ = ["GraphDefinition", "GraphNode", "NestedGraphNodeDefinition"]

"""Immutable, versioned graph definitions after builder snapshotting."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.graph.edge import Edge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import GraphOutputDeclarations, InputBindings
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class NestedGraphNodeDefinition(Generic[GraphValueT]):
    node_id: GraphNodeId
    graph: "GraphDefinition[GraphValueT]"
    inputs: InputBindings[GraphValueT]


GraphNode: TypeAlias = CallableNodeDefinition[GraphValueT] | NestedGraphNodeDefinition[GraphValueT]


@dataclass(frozen=True, slots=True)
class GraphDefinition(Generic[GraphValueT]):
    definition_id: GraphDefinitionId
    version: GraphDefinitionVersion
    nodes: tuple[GraphNode[GraphValueT], ...]
    edges: tuple[Edge, ...]
    entries: tuple[GraphNodeId, ...]
    outputs: GraphOutputDeclarations[GraphValueT]
    resources: tuple[ResourceDefinition, ...] = ()
    resume_input: ResumeInputBinding[GraphValueT] | None = None


__all__: list[str] = []

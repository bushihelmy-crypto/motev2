"""Graph definition compiler."""

from typing import TypeVar

from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge, RouteId
from mote_kernel.execution.graph.identity import NodeId
from mote_kernel.execution.graph.node import NodeDefinition
from mote_kernel.execution.graph.topology import (
    CompiledGraph,
    immutable_join_mapping,
    immutable_mapping,
    immutable_node_mapping,
    immutable_resource_mapping,
    immutable_route_mapping,
)
from mote_kernel.execution.graph.validation import validate_graph

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def compile_graph(definition: GraphDefinition[InputT, OutputT]) -> CompiledGraph[InputT, OutputT]:
    """Validate and compile a graph definition into deterministic indexes."""

    validate_graph(definition)
    resource_order = tuple(
        resource.resource_id for resource in sorted(definition.resources, key=lambda resource: resource.order)
    )
    resource_positions = {resource_id: position for position, resource_id in enumerate(resource_order)}
    nodes = {
        node.node_id: (
            NodeDefinition(
                node.node_id,
                node.node,
                tuple(sorted(node.resources, key=resource_positions.__getitem__)),
            )
            if isinstance(node, NodeDefinition)
            else node
        )
        for node in definition.nodes
    }
    direct_targets: dict[NodeId, list[NodeId]] = {node_id: [] for node_id in nodes}
    conditional_targets: dict[NodeId, dict[RouteId, NodeId]] = {node_id: {} for node_id in nodes}
    joins_by_source: dict[NodeId, list[JoinEdge]] = {node_id: [] for node_id in nodes}

    for edge in definition.edges:
        if isinstance(edge, DirectEdge):
            if edge.target != END:
                direct_targets[edge.source].append(edge.target)
        elif isinstance(edge, ConditionalEdge):
            routes = conditional_targets[edge.source]
            routes[edge.route] = edge.target
        else:
            normalized_edge = JoinEdge(tuple(sorted(edge.sources)), edge.target)
            for source in edge.sources:
                joins_by_source[source].append(normalized_edge)

    return CompiledGraph(
        definition_id=definition.definition_id,
        version=definition.version,
        nodes=immutable_node_mapping(nodes),
        entries=tuple(sorted(definition.entries)),
        direct_targets=immutable_mapping(
            {node_id: tuple(sorted(targets)) for node_id, targets in direct_targets.items()}
        ),
        conditional_targets=immutable_route_mapping(conditional_targets),
        joins_by_source=immutable_join_mapping(joins_by_source),
        resources=immutable_resource_mapping({resource.resource_id: resource for resource in definition.resources}),
        resource_order=resource_order,
        resolution=definition.resolution,
    )


__all__ = ["compile_graph"]

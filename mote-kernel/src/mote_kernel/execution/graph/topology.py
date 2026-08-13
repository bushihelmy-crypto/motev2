"""Compiled graph topology and indexes."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar

from mote_kernel.execution.graph.definition import GraphNode
from mote_kernel.execution.graph.edge import JoinEdge
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state.identity import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class CompiledGraph(Generic[InputT, OutputT]):
    definition_id: GraphDefinitionId
    version: GraphDefinitionVersion
    nodes: Mapping[GraphNodeId, GraphNode[InputT, OutputT]]
    entries: tuple[GraphNodeId, ...]
    direct_targets: Mapping[GraphNodeId, tuple[GraphNodeId, ...]]
    conditional_targets: Mapping[GraphNodeId, Mapping[GraphRouteId, GraphNodeId]]
    joins_by_source: Mapping[GraphNodeId, tuple[JoinEdge, ...]]
    resources: Mapping[ResourceId, ResourceDefinition]
    resource_order: tuple[ResourceId, ...]
    resume_input: ResumeInputBinding[InputT] | None


def immutable_mapping(
    values: dict[GraphNodeId, tuple[GraphNodeId, ...]],
) -> Mapping[GraphNodeId, tuple[GraphNodeId, ...]]:
    return MappingProxyType(dict(sorted(values.items())))


def immutable_route_mapping(
    values: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]],
) -> Mapping[GraphNodeId, Mapping[GraphRouteId, GraphNodeId]]:
    return MappingProxyType(
        {node_id: MappingProxyType(dict(sorted(routes.items()))) for node_id, routes in sorted(values.items())}
    )


def immutable_node_mapping(
    values: dict[GraphNodeId, GraphNode[InputT, OutputT]],
) -> Mapping[GraphNodeId, GraphNode[InputT, OutputT]]:
    return MappingProxyType(dict(sorted(values.items())))


def immutable_resource_mapping(
    values: dict[ResourceId, ResourceDefinition],
) -> Mapping[ResourceId, ResourceDefinition]:
    return MappingProxyType(dict(sorted(values.items())))


def immutable_join_mapping(
    values: dict[GraphNodeId, list[JoinEdge]],
) -> Mapping[GraphNodeId, tuple[JoinEdge, ...]]:
    return MappingProxyType(
        {
            node_id: tuple(sorted(edges, key=lambda edge: (edge.target, edge.sources)))
            for node_id, edges in sorted(values.items())
        }
    )


__all__ = ["CompiledGraph"]

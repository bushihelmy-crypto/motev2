"""Compiled graph topology and indexes."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar

from mote_kernel.execution.graph.definition import GraphDefinitionId, GraphDefinitionVersion, GraphNode
from mote_kernel.execution.graph.edge import JoinEdge, RouteId
from mote_kernel.execution.graph.identity import NodeId

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class CompiledGraph(Generic[InputT, OutputT]):
    """An immutable graph topology with deterministic runtime indexes."""

    definition_id: GraphDefinitionId
    version: GraphDefinitionVersion
    nodes: Mapping[NodeId, GraphNode[InputT, OutputT]]
    entries: tuple[NodeId, ...]
    direct_targets: Mapping[NodeId, tuple[NodeId, ...]]
    conditional_targets: Mapping[NodeId, Mapping[RouteId, NodeId]]
    joins_by_source: Mapping[NodeId, tuple[JoinEdge, ...]]


def immutable_mapping(values: dict[NodeId, tuple[NodeId, ...]]) -> Mapping[NodeId, tuple[NodeId, ...]]:
    """Return a read-only copy of a node index."""

    return MappingProxyType(dict(sorted(values.items())))


def immutable_route_mapping(
    values: dict[NodeId, dict[RouteId, NodeId]],
) -> Mapping[NodeId, Mapping[RouteId, NodeId]]:
    """Return a deeply read-only copy of a conditional-route index."""

    return MappingProxyType(
        {node_id: MappingProxyType(dict(sorted(routes.items()))) for node_id, routes in sorted(values.items())}
    )


def immutable_node_mapping(
    values: dict[NodeId, GraphNode[InputT, OutputT]],
) -> Mapping[NodeId, GraphNode[InputT, OutputT]]:
    """Return a read-only copy of the node index."""

    return MappingProxyType(dict(sorted(values.items())))


def immutable_join_mapping(values: dict[NodeId, list[JoinEdge]]) -> Mapping[NodeId, tuple[JoinEdge, ...]]:
    """Return a read-only join index with deterministic edge order."""

    return MappingProxyType(
        {
            node_id: tuple(sorted(edges, key=lambda edge: (edge.target, edge.sources)))
            for node_id, edges in sorted(values.items())
        }
    )


__all__ = ["CompiledGraph"]

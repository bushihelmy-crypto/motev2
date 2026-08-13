"""Unique compiled-topology routing validator and frontier resolver."""

from typing import TypeVar

from mote_kernel.execution.errors import (
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    UnknownRouteError,
)
from mote_kernel.execution.graph import END, CompiledGraph
from mote_kernel.execution.graph.edge import JoinEdge
from mote_kernel.state.graph_state import (
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    GraphFrontierResolution,
    GraphJoinProgress,
    GraphNodeId,
    GraphRoutingContribution,
    SelectGraphRoute,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def validate_routing_contribution(
    graph: CompiledGraph[InputT, OutputT],
    node_id: GraphNodeId,
    contribution: GraphRoutingContribution,
) -> None:
    if node_id not in graph.nodes:
        raise InvalidRoutingCommandError("routing contribution references an unknown node")
    if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        contribution, ContinueGraphRouting | SelectGraphRoute
    ):
        raise InvalidRoutingCommandError("routing contribution has an unsupported variant")
    conditional = graph.conditional_targets[node_id]
    if isinstance(contribution, ContinueGraphRouting):
        if conditional:
            raise InvalidRoutingCommandError("a conditional node must select one declared route")
    else:
        if not conditional:
            raise InvalidRoutingCommandError("a non-conditional node cannot select a route")
        if contribution.route not in conditional:
            raise UnknownRouteError("node selected an unknown conditional route")


def _join_key(sources: tuple[GraphNodeId, ...], target: GraphNodeId) -> tuple[tuple[GraphNodeId, ...], GraphNodeId]:
    return (sources, target)


def _declared_joins(
    graph: CompiledGraph[InputT, OutputT],
) -> dict[tuple[tuple[GraphNodeId, ...], GraphNodeId], JoinEdge]:
    return {_join_key(edge.sources, edge.target): edge for edges in graph.joins_by_source.values() for edge in edges}


def resolve_routing(
    graph: CompiledGraph[InputT, OutputT],
    contributions: tuple[tuple[GraphNodeId, GraphRoutingContribution], ...],
    prior_join_progress: tuple[GraphJoinProgress, ...],
) -> GraphFrontierResolution:
    declared = _declared_joins(graph)
    arrivals: dict[tuple[tuple[GraphNodeId, ...], GraphNodeId], set[GraphNodeId]] = {}
    for progress in prior_join_progress:
        key = _join_key(progress.sources, progress.target)
        edge = declared.get(key)
        if edge is None or key in arrivals or not progress.arrived or not progress.arrived < frozenset(edge.sources):
            raise JoinProgressError("snapshot contains invalid join progress")
        arrivals[key] = set(progress.arrived)
    next_nodes: set[GraphNodeId] = set()
    for node_id, contribution in contributions:
        validate_routing_contribution(graph, node_id, contribution)
        next_nodes.update(graph.direct_targets[node_id])
        if isinstance(contribution, SelectGraphRoute):
            target = graph.conditional_targets[node_id][contribution.route]
            if target != END:
                next_nodes.add(target)
        for edge in graph.joins_by_source[node_id]:
            arrivals.setdefault(_join_key(edge.sources, edge.target), set()).add(node_id)
    remaining: list[GraphJoinProgress] = []
    for key in sorted(arrivals):
        edge = declared[key]
        arrived = arrivals[key]
        if arrived == set(edge.sources):
            if edge.target != END:
                next_nodes.add(edge.target)
        else:
            remaining.append(GraphJoinProgress(edge.sources, edge.target, frozenset(arrived)))
    if not next_nodes:
        if remaining:
            raise RoutingDeadlockError("partial join progress has no next task able to complete it")
        return CompleteGraphFrontier()
    return AdvanceGraphFrontier(tuple(sorted(next_nodes)), tuple(remaining))


__all__ = ["resolve_routing", "validate_routing_contribution"]

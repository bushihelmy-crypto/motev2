"""Deterministic static-edge and join routing."""

from dataclasses import dataclass
from typing import TypeVar

from mote_kernel.execution.engine.collector import CollectedResults
from mote_kernel.execution.errors import (
    InvalidRoutingCommandError,
    JoinProgressError,
    RoutingDeadlockError,
    UnknownRouteError,
)
from mote_kernel.execution.graph import END, CompiledGraph, NodeId
from mote_kernel.execution.graph.command import Continue
from mote_kernel.execution.graph.edge import JoinEdge
from mote_kernel.execution.snapshot import ExecutionSnapshot, JoinProgress

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The next frontier and still-pending join arrivals."""

    frontier: tuple[NodeId, ...]
    join_progress: tuple[JoinProgress, ...]


def _join_key(sources: tuple[NodeId, ...], target: NodeId) -> tuple[tuple[NodeId, ...], NodeId]:
    return (sources, target)


def _declared_joins(graph: CompiledGraph[InputT, OutputT]) -> dict[tuple[tuple[NodeId, ...], NodeId], JoinEdge]:
    joins: dict[tuple[tuple[NodeId, ...], NodeId], JoinEdge] = {}
    for node_id in graph.nodes:
        for edge in graph.joins_by_source[node_id]:
            joins[_join_key(edge.sources, edge.target)] = edge
    return joins


def route_results(
    graph: CompiledGraph[InputT, OutputT],
    snapshot: ExecutionSnapshot,
    collected: CollectedResults[OutputT],
) -> RoutingDecision:
    """Route one successful superstep without executing nodes or mutating state."""

    if collected.failure is not None:
        raise InvalidRoutingCommandError("failed collections cannot be routed")
    success_nodes = tuple(success.task.node_id for success in collected.successes)
    if len(success_nodes) != len(set(success_nodes)) or tuple(sorted(success_nodes)) != tuple(
        sorted(snapshot.frontier)
    ):
        raise InvalidRoutingCommandError("a successful collection must cover the snapshot frontier")
    declared_joins = _declared_joins(graph)
    arrivals: dict[tuple[tuple[NodeId, ...], NodeId], set[NodeId]] = {}
    for progress in snapshot.join_progress:
        key = _join_key(progress.sources, progress.target)
        edge = declared_joins.get(key)
        if edge is None or not progress.arrived or not progress.arrived < frozenset(edge.sources):
            raise JoinProgressError("snapshot contains invalid join progress")
        if key in arrivals:
            raise JoinProgressError("snapshot repeats join progress")
        arrivals[key] = set(progress.arrived)
    next_nodes: set[NodeId] = set()
    for success in collected.successes:
        source = success.task.node_id
        if (
            source not in graph.nodes
            or source not in snapshot.frontier
            or success.task.run_id != snapshot.run_id
            or success.task.superstep != snapshot.superstep
        ):
            raise InvalidRoutingCommandError("collected success does not belong to the snapshot frontier")
        if isinstance(success.routing, Continue):
            if graph.conditional_targets[source]:
                raise InvalidRoutingCommandError("a node with conditional edges must select a route")
            next_nodes.update(graph.direct_targets[source])
        else:
            next_nodes.update(graph.direct_targets[source])
            target = graph.conditional_targets[source].get(success.routing.route)
            if target is None:
                raise UnknownRouteError("node selected an unknown conditional route")
            if target != END:
                next_nodes.add(target)
        for edge in graph.joins_by_source[source]:
            key = _join_key(edge.sources, edge.target)
            arrivals.setdefault(key, set()).add(source)
    remaining: list[JoinProgress] = []
    for key in sorted(arrivals):
        edge = declared_joins[key]
        arrived = arrivals[key]
        if arrived == set(edge.sources):
            if edge.target != END:
                next_nodes.add(edge.target)
        else:
            remaining.append(JoinProgress(edge.sources, edge.target, frozenset(arrived)))
    if not next_nodes and remaining:
        raise RoutingDeadlockError("partial join progress has no next task able to complete it")
    return RoutingDecision(tuple(sorted(next_nodes)), tuple(remaining))


__all__: list[str] = []

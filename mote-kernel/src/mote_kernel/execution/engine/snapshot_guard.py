"""Compiled-graph compatibility checks for authoritative snapshots."""

from typing import TypeVar

from mote_kernel.execution.engine.resume_input import require_resume_input_binding
from mote_kernel.execution.engine.routing import validate_routing_contribution
from mote_kernel.execution.errors import InvalidExecutionSnapshotError, SnapshotMismatchError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRunState,
    GraphRunStatus,
    routing_contributions,
    validate_graph_run_state,
)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
GraphDefinitionKey = tuple[GraphDefinitionId, GraphDefinitionVersion]


def require_snapshot_matches_graph(
    graph: CompiledGraph[InputT, OutputT],
    state: GraphRunState,
    parent_nodes: frozenset[tuple[GraphDefinitionKey, GraphNodeId]] | None = None,
) -> None:
    validate_graph_run_state(state)
    if state.definition_id != graph.definition_id or state.definition_version != graph.version:
        raise SnapshotMismatchError("graph run does not match the compiled graph identity and version")
    unknown = tuple(node.node_id for node in state.frontier.nodes if node.node_id not in graph.nodes)
    if unknown:
        raise InvalidExecutionSnapshotError(f"snapshot frontier contains unknown nodes: {unknown!r}")
    if state.status is not GraphRunStatus.RUNNING:
        return
    if (
        parent_nodes is not None
        and state.parent is not None
        and (
            (state.definition_id, state.definition_version),
            state.parent.node_id,
        )
        not in parent_nodes
    ):
        raise InvalidExecutionSnapshotError("snapshot parent activation does not match a compiled parent node")
    declared_joins = {(edge.sources, edge.target) for edges in graph.joins_by_source.values() for edge in edges}
    if any((progress.sources, progress.target) not in declared_joins for progress in state.join_progress):
        raise InvalidExecutionSnapshotError("snapshot references unknown join progress")
    require_resume_input_binding(graph, state)
    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)


__all__ = ["require_snapshot_matches_graph"]

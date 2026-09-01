"""Compiled-graph compatibility checks for authoritative snapshots."""

from typing import TypeVar

from mote_kernel.execution.engine.resume_input import require_resume_input_binding
from mote_kernel.execution.engine.routing import _declared_joins, validate_routing_contribution
from mote_kernel.execution.errors import InvalidExecutionSnapshotError, SnapshotMismatchError
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.state.graph_state import (
    GraphRunState,
    GraphRunStatus,
    PendingGraphNode,
    routing_contributions,
    validate_graph_run_state,
)

GraphValueT = TypeVar("GraphValueT")


def require_snapshot_matches_graph(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
) -> None:
    validate_graph_run_state(state)
    if state.definition_id != graph.definition_id or state.definition_version != graph.version:
        raise SnapshotMismatchError("graph run does not match the compiled graph identity and version")
    unknown = tuple(node.node_id for node in state.frontier.nodes if node.node_id not in graph.nodes)
    if unknown:
        raise InvalidExecutionSnapshotError(f"snapshot frontier contains unknown nodes: {unknown!r}")
    if state.status is not GraphRunStatus.RUNNING:
        return
    declared_joins = _declared_joins(graph)
    if any((progress.sources, progress.target) not in declared_joins for progress in state.join_progress):
        raise InvalidExecutionSnapshotError("snapshot references unknown join progress")
    require_resume_input_binding(graph, state)
    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)
    if state.execution is None:
        return
    required = {
        node.node_id: definition.resources
        for node in state.frontier.nodes
        if isinstance(node.settlement, PendingGraphNode)
        and isinstance(definition := graph.nodes[node.node_id], CallableNodeDefinition)
        and definition.resources
    }
    resources = state.resources
    if not required:
        if resources is not None:
            raise InvalidExecutionSnapshotError("resource-free pending nodes cannot retain acquisitions")
        return
    if resources is None or tuple(lock.resource_id for lock in resources.resources) != graph.transition.resource_order:
        raise InvalidExecutionSnapshotError("active resource participants require the compiled resource snapshot")
    acquisitions = {item.node_id: item.required for item in resources.acquisitions}
    if acquisitions != required:
        raise InvalidExecutionSnapshotError("resource acquisitions do not exactly match pending compiled requirements")


def require_scoped_snapshot_matches_graph(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
) -> None:
    """Require one snapshot to belong to one compiled scoped run."""

    require_snapshot_matches_graph(graph, state)
    if graph.definition_scope != scope_run.scope or state.run_id != scope_run.graph_run_id:
        raise SnapshotMismatchError("scope-run coordinate does not match its compiled graph state")
    if not scope_run.scope:
        if state.parent is not None:
            raise SnapshotMismatchError("root graph state cannot carry a parent activation")
        return
    if state.parent is None or state.parent.node_id != scope_run.scope[-1]:
        raise SnapshotMismatchError("nested graph state does not match its compiled definition scope")


__all__ = ["require_scoped_snapshot_matches_graph", "require_snapshot_matches_graph"]

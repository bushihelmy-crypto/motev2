"""Compiled-graph compatibility checks for authoritative snapshots."""

from typing import TypeVar

from mote_kernel.execution.engine.resume_input import require_resume_input_binding
from mote_kernel.execution.engine.routing import _declared_joins, validate_routing_contribution
from mote_kernel.execution.errors import InvalidExecutionSnapshotError, SnapshotMismatchError
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRunState,
    GraphRunStatus,
    PendingGraphNode,
    routing_contributions,
    validate_graph_run_state,
)

GraphValueT = TypeVar("GraphValueT")
GraphDefinitionKey = tuple[GraphDefinitionId, GraphDefinitionVersion]


def require_snapshot_matches_graph(
    graph: CompiledGraph[GraphValueT],
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
    declared_joins = _declared_joins(graph)
    if any((progress.sources, progress.target) not in declared_joins for progress in state.join_progress):
        raise InvalidExecutionSnapshotError("snapshot references unknown join progress")
    require_resume_input_binding(graph, state)
    for node_id, contribution in routing_contributions(state.frontier):
        validate_routing_contribution(graph, node_id, contribution)
    if state.execution is not None:
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
        if resources is None or tuple(lock.resource_id for lock in resources.resources) != graph.resource_order:
            raise InvalidExecutionSnapshotError("active resource participants require the compiled resource snapshot")
        acquisitions = {item.node_id: item for item in resources.acquisitions}
        if acquisitions.keys() != required.keys() or any(
            acquisitions[node_id].required != requirements for node_id, requirements in required.items()
        ):
            raise InvalidExecutionSnapshotError(
                "resource acquisitions do not exactly match pending compiled requirements"
            )


__all__ = ["require_snapshot_matches_graph"]

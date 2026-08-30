"""Execution-owned scoped graph-run identities."""

from dataclasses import dataclass

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId, ParentGraphActivation, child_graph_run_id


@dataclass(frozen=True, slots=True, order=True)
class ScopeRunCoordinate:
    scope: tuple[GraphNodeId, ...]
    graph_run_id: GraphRunId

    def __post_init__(self) -> None:
        if any(not segment for segment in self.scope) or not self.graph_run_id:
            raise SnapshotMismatchError("scope-run coordinate requires canonical scope and run identity")


@dataclass(frozen=True, slots=True, order=True)
class StableActivation:
    scope_run: ScopeRunCoordinate
    superstep: int
    node_id: GraphNodeId

    def __post_init__(self) -> None:
        if self.superstep < 0 or not self.node_id:
            raise SnapshotMismatchError("stable activation requires a valid superstep and node identity")


def root_scope_run(run_id: GraphRunId) -> ScopeRunCoordinate:
    return ScopeRunCoordinate((), run_id)


def child_scope_run(
    parent_scope_run: ScopeRunCoordinate,
    parent_superstep: int,
    nested_node_id: GraphNodeId,
) -> ScopeRunCoordinate:
    run_id = child_graph_run_id(parent_scope_run.graph_run_id, parent_superstep, nested_node_id)
    return ScopeRunCoordinate((*parent_scope_run.scope, nested_node_id), run_id)


def child_scope_run_for_activation(
    parent_scope_run: ScopeRunCoordinate,
    parent: ParentGraphActivation,
) -> ScopeRunCoordinate:
    if parent.run_id != parent_scope_run.graph_run_id:
        raise SnapshotMismatchError("parent activation does not belong to its scope-run coordinate")
    return child_scope_run(parent_scope_run, parent.superstep, parent.node_id)


__all__ = [
    "ScopeRunCoordinate",
    "StableActivation",
    "child_scope_run",
    "child_scope_run_for_activation",
    "root_scope_run",
]

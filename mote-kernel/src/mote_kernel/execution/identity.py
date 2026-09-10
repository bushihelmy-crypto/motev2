"""Execution-owned scoped graph-run identities."""

from dataclasses import dataclass

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.state.graph_state import GraphActivationIdentity, GraphNodeId, GraphRunId, child_graph_run_id
from mote_kernel.state.graph_state.identity import is_canonical_identity


@dataclass(frozen=True, slots=True, order=True)
class ScopeRunCoordinate:
    scope: tuple[GraphNodeId, ...]
    graph_run_id: GraphRunId

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not tuple
            or any(not is_canonical_identity(segment) for segment in self.scope)
            or not is_canonical_identity(self.graph_run_id)
        ):
            raise SnapshotMismatchError("scope-run coordinate requires canonical scope and run identity")


@dataclass(frozen=True, slots=True, order=True)
class StableActivation:
    scope_run: ScopeRunCoordinate
    superstep: int
    node_id: GraphNodeId

    def __post_init__(self) -> None:
        if (
            type(self.scope_run) is not ScopeRunCoordinate
            or type(self.superstep) is not int
            or self.superstep < 0
            or not is_canonical_identity(self.node_id)
        ):
            raise SnapshotMismatchError("stable activation requires a valid superstep and node identity")


def root_scope_run(run_id: GraphRunId) -> ScopeRunCoordinate:
    return ScopeRunCoordinate((), run_id)


def stable_activation(
    scope_run: ScopeRunCoordinate,
    activation: GraphActivationIdentity,
) -> StableActivation:
    """Project the state-owned identity into its scoped execution lookup key."""

    if activation.run_id != scope_run.graph_run_id:
        raise SnapshotMismatchError("activation does not belong to its scope-run coordinate")
    return StableActivation(scope_run, activation.superstep, activation.node_id)


def child_scope_run(
    parent_scope_run: ScopeRunCoordinate,
    parent_superstep: int,
    nested_node_id: GraphNodeId,
) -> ScopeRunCoordinate:
    run_id = child_graph_run_id(parent_scope_run.graph_run_id, parent_superstep, nested_node_id)
    return ScopeRunCoordinate((*parent_scope_run.scope, nested_node_id), run_id)


def child_scope_run_for_activation(
    parent_scope_run: ScopeRunCoordinate,
    parent: GraphActivationIdentity,
) -> ScopeRunCoordinate:
    if parent.run_id != parent_scope_run.graph_run_id:
        raise SnapshotMismatchError("parent activation does not belong to its scope-run coordinate")
    return child_scope_run(parent_scope_run, parent.superstep, parent.node_id)


def parent_activation_for_child(
    child_scope_run: ScopeRunCoordinate,
    parent: GraphActivationIdentity,
) -> StableActivation:
    """Project a state-owned parent identity from its canonical child scope.

    ``GraphRunState.parent`` owns the durable activation identity.  A child
    binding only needs its scope coordinate in addition to that state; the
    scoped execution lookup key is derived here and the deterministic
    parent-to-child run identity is checked at the same boundary.
    """

    if type(child_scope_run) is not ScopeRunCoordinate or type(parent) is not GraphActivationIdentity:
        raise SnapshotMismatchError("child lineage binding has inconsistent parent coordinates")
    scope = child_scope_run.scope
    if type(scope) is not tuple or not scope or scope[-1] != parent.node_id:
        raise SnapshotMismatchError("child lineage binding has inconsistent parent coordinates")
    parent_scope_run = ScopeRunCoordinate(scope[:-1], parent.run_id)
    if child_scope_run_for_activation(parent_scope_run, parent) != child_scope_run:
        raise SnapshotMismatchError("child lineage binding has inconsistent parent coordinates")
    return stable_activation(parent_scope_run, parent)


__all__ = [
    "ScopeRunCoordinate",
    "StableActivation",
    "child_scope_run",
    "child_scope_run_for_activation",
    "parent_activation_for_child",
    "root_scope_run",
    "stable_activation",
]

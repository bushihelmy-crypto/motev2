import pytest

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.identity import (
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId, ParentGraphActivation


@pytest.mark.parametrize(
    ("scope", "run_id"),
    [
        ((GraphNodeId(""),), GraphRunId("run")),
        ((), GraphRunId("")),
    ],
)
def test_scope_run_coordinate_rejects_noncanonical_identity(
    scope: tuple[GraphNodeId, ...],
    run_id: GraphRunId,
) -> None:
    with pytest.raises(SnapshotMismatchError, match="canonical scope and run"):
        ScopeRunCoordinate(scope, run_id)


@pytest.mark.parametrize(
    ("superstep", "node_id"),
    [(-1, GraphNodeId("node")), (0, GraphNodeId(""))],
)
def test_stable_activation_rejects_invalid_execution_position(
    superstep: int,
    node_id: GraphNodeId,
) -> None:
    with pytest.raises(SnapshotMismatchError, match="valid superstep and node"):
        StableActivation(root_scope_run(GraphRunId("run")), superstep, node_id)


def test_child_scope_requires_parent_from_the_same_scoped_run() -> None:
    scope_run = root_scope_run(GraphRunId("run"))
    foreign_parent = ParentGraphActivation(GraphRunId("other"), 0, GraphNodeId("nested"))

    with pytest.raises(SnapshotMismatchError, match="does not belong"):
        child_scope_run_for_activation(scope_run, foreign_parent)

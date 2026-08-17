import pytest
from tests.execution.engine.factories import running_state

from mote_kernel.execution.errors import GraphValueUnavailableError, SnapshotMismatchError
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, FrameKind, canonical_nominal_type
from mote_kernel.execution.graph.values import NamedValue, _make_graph_output_view
from mote_kernel.execution.identity import ScopeRunCoordinate, root_scope_run
from mote_kernel.execution.run_context import (
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedChildBoundary,
    ScopedFrameIndex,
    _new_context,
    _new_family_identity,
)
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId


def test_child_boundary_lookup_distinguishes_repeated_scoped_runs() -> None:
    descriptor = FrameDescriptorIdentity("child.graph", 1, FrameKind.GRAPH_OUTPUT, 0)
    frame = _make_graph_output_view(
        (NamedValue("value", "output"),),
        (("value", canonical_nominal_type(str)),),
    )
    first_coordinate: ChildBoundaryAvailabilityCoordinate[str] = ChildBoundaryAvailabilityCoordinate(
        root_scope_run(GraphRunId("child-a")),
        descriptor,
    )
    second_coordinate: ChildBoundaryAvailabilityCoordinate[str] = ChildBoundaryAvailabilityCoordinate(
        root_scope_run(GraphRunId("child-b")),
        descriptor,
    )
    first = ConfirmedChildBoundary(first_coordinate, frame)
    second = ConfirmedChildBoundary(second_coordinate, frame)

    index: ScopedFrameIndex[str] = ScopedFrameIndex()
    index = index.add_child_boundary(second).add_child_boundary(first)

    assert index.child_boundaries == (first, second)
    assert index.lookup(second_coordinate) is second


def test_run_context_rejects_access_or_replacement_before_child_start_acknowledgement() -> None:
    root = running_state()
    context = _new_context(
        _new_family_identity(),
        root,
        ScopedFrameIndex(),
        recovered=False,
    )
    missing = ScopeRunCoordinate((GraphNodeId("child"),), GraphRunId("missing-child-run"))

    assert context.state_at(root_scope_run(root.run_id)) is root
    with pytest.raises(GraphValueUnavailableError, match="child state is unavailable"):
        context.state_at(missing)
    with pytest.raises(SnapshotMismatchError, match="before its acknowledged start"):
        context.replace_state(missing, root)

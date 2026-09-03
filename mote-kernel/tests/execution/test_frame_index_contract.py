from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, FrameKind, normalize_output_declarations
from mote_kernel.execution.graph.values import (
    NamedValue,
    _make_graph_input_frame,
    _make_graph_output_view,
    _make_node_output_frame,
)
from mote_kernel.execution.identity import StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphNodeId,
    GraphRunId,
)


def test_child_boundary_lookup_distinguishes_repeated_scoped_runs() -> None:
    descriptor = FrameDescriptorIdentity("child.graph", 1, FrameKind.GRAPH_OUTPUT, 0)
    frame = _make_graph_output_view(
        (NamedValue("value", "output"),),
        normalize_output_declarations({"value": str}),
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


def test_scoped_frame_index_is_the_single_availability_source_for_all_segments() -> None:
    scope_run = root_scope_run(GraphRunId("candidate-run"))
    descriptor = FrameDescriptorIdentity("candidate.graph", 1, FrameKind.GRAPH_INPUT, 0)
    graph_input_coordinate: GraphInputAvailabilityCoordinate[str] = GraphInputAvailabilityCoordinate(
        scope_run, descriptor
    )
    declarations = normalize_output_declarations({"value": str})
    frame = _make_graph_input_frame(Graph.values(value="input"), declarations)
    index = ScopedFrameIndex[str]().add_graph_input(AdmittedGraphInput(graph_input_coordinate, frame))
    publication_coordinate: PublicationAvailabilityCoordinate[str] = PublicationAvailabilityCoordinate(
        StableActivation(scope_run, 0, GraphNodeId("source")),
        FrameDescriptorIdentity("candidate.graph", 1, FrameKind.NODE_OUTPUT, 0),
    )
    publication = ConfirmedPublication(
        publication_coordinate,
        _make_node_output_frame(Graph.values(value="published"), declarations),
        2,
        ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("claim"))),
    )
    index = index.add_publication(publication)

    assert index.has_graph_input(graph_input_coordinate)
    assert index.has_publication(publication_coordinate)
    assert not index.has_resume_input(
        ResumeInputAvailabilityCoordinate(
            publication_coordinate.activation,
            FrameDescriptorIdentity("candidate.graph", 1, FrameKind.NODE_INPUT, 0),
        )
    )
    assert not index.has_child_boundary(
        ChildBoundaryAvailabilityCoordinate(
            scope_run,
            FrameDescriptorIdentity("candidate.graph", 1, FrameKind.GRAPH_OUTPUT, 0),
        )
    )

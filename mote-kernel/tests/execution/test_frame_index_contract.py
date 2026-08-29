from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, FrameKind, canonical_nominal_type
from mote_kernel.execution.graph.values import (
    NamedValue,
    _make_graph_input_frame,
    _make_graph_output_view,
    _make_node_output_frame,
)
from mote_kernel.execution.identity import StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedSubstitution,
    CandidateFrameAvailability,
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedChildBoundary,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ResumeInputAvailabilityCoordinate,
    ScopedFrameIndex,
    SkipSubstitutionProvenance,
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


def test_candidate_availability_delegates_non_publication_segments_and_overlays_publications() -> None:
    scope_run = root_scope_run(GraphRunId("candidate-run"))
    descriptor = FrameDescriptorIdentity("candidate.graph", 1, FrameKind.GRAPH_INPUT, 0)
    graph_input_coordinate: GraphInputAvailabilityCoordinate[str] = GraphInputAvailabilityCoordinate(
        scope_run, descriptor
    )
    frame = _make_graph_input_frame(Graph.values(value="input"), (("value", canonical_nominal_type(str)),))
    confirmed = ScopedFrameIndex[str]().add_graph_input(AdmittedGraphInput(graph_input_coordinate, frame))
    publication_coordinate: PublicationAvailabilityCoordinate[str] = PublicationAvailabilityCoordinate(
        StableActivation(scope_run, 0, GraphNodeId("source")),
        FrameDescriptorIdentity("candidate.graph", 1, FrameKind.NODE_OUTPUT, 0),
    )
    substitution = AdmittedSubstitution(
        publication_coordinate,
        _make_node_output_frame(Graph.values(value="replacement"), (("value", canonical_nominal_type(str)),)),
        SkipSubstitutionProvenance(),
        2,
    )
    availability = CandidateFrameAvailability(confirmed, (substitution,))

    assert availability.has_graph_input(graph_input_coordinate)
    assert availability.has_publication(publication_coordinate)
    assert not availability.has_resume_input(
        ResumeInputAvailabilityCoordinate(
            publication_coordinate.activation,
            FrameDescriptorIdentity("candidate.graph", 1, FrameKind.NODE_INPUT, 0),
        )
    )
    assert not availability.has_child_boundary(
        ChildBoundaryAvailabilityCoordinate(
            scope_run,
            FrameDescriptorIdentity("candidate.graph", 1, FrameKind.GRAPH_OUTPUT, 0),
        )
    )

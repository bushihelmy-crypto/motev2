from dataclasses import replace

import pytest
from tests.execution.engine.factories import direct, join, running_state, topology

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.resume_admission import ScopedResumeCandidate, admit_resume_candidates
from mote_kernel.execution.errors import GraphValuePublicationError, GraphValueUnavailableError, SnapshotMismatchError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    NodeOutputRef,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import _make_node_output_frame
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation, root_scope_run
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedSubstitution,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
    SkipSubstitutionProvenance,
)
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    FailedGraphNode,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphJoinProgress,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphSkipReason,
    ResumeGraphNodes,
    SkipFailedNode,
    SkippedGraphNode,
    reduce_graph_run,
)


async def _echo(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


def _data_graph(*, target_uses_graph_input: bool) -> CompiledGraph[str]:
    source = CallableNodeDefinition(
        GraphNodeId("source"),
        _echo,
        normalize_input_bindings({}),
        normalize_output_declarations({"value": str}),
    )
    inputs: dict[str, GraphInputRef[str] | NodeOutputRef] = {"source": Graph.node_output("source", "value")}
    if target_uses_graph_input:
        inputs["required"] = Graph.graph_input("required", str)
    target = CallableNodeDefinition(
        GraphNodeId("target"),
        _echo,
        normalize_input_bindings(inputs),
        normalize_output_declarations({}),
    )
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("test.graph"),
            version=GraphDefinitionVersion(1),
            nodes=(source, target),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )


def _substitution(graph: CompiledGraph[str], state: GraphRunState) -> AdmittedSubstitution[str]:
    publication = graph.publications[GraphNodeId("source")]
    return AdmittedSubstitution(
        PublicationAvailabilityCoordinate(
            StableActivation(root_scope_run(state.run_id), state.superstep, GraphNodeId("source")),
            publication.descriptor.identity,
        ),
        _make_node_output_frame(
            Graph.values(value="replacement"),
            tuple(
                (declaration.name, declaration.descriptor)
                for declaration in publication.descriptor.declarations.entries
            ),
        ),
        SkipSubstitutionProvenance(),
        state.revision + 1,
    )


def _skipped_successor(state: GraphRunState, node_id: str = "source") -> GraphRunState:
    return replace(
        state,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    GraphNodeId(node_id),
                    SkippedGraphNode(
                        GraphFailure("failed"),
                        GraphSkipReason("replacement"),
                        ContinueGraphRouting(),
                    ),
                ),
            )
        ),
    )


def _skip_action(node_id: str = "source") -> SkipFailedNode:
    return SkipFailedNode(GraphNodeId(node_id), GraphSkipReason("replacement"), ContinueGraphRouting())


def _failed_state(state: GraphRunState, node_id: str = "source") -> GraphRunState:
    return replace(
        state,
        frontier=GraphFrontierState(
            (GraphFrontierNode(GraphNodeId(node_id), FailedGraphNode(GraphFailure("failed"))),)
        ),
    )


def _candidate(
    graph: CompiledGraph[str],
    state: GraphRunState,
    substitutions: tuple[AdmittedSubstitution[str], ...],
    actions: tuple[SkipFailedNode, ...] = (),
    *,
    scope_run: ScopeRunCoordinate | None = None,
) -> ScopedResumeCandidate[str]:
    command = ResumeGraphNodes(state.revision, actions)
    return ScopedResumeCandidate(
        graph,
        root_scope_run(state.run_id) if scope_run is None else scope_run,
        state,
        reduce_graph_run(state, command),
        substitutions,
        command,
    )


def test_resume_admission_rejects_duplicate_and_confirmed_substitution_coordinates() -> None:
    graph = topology("source")
    state = _failed_state(running_state(frontier=("source",)))
    substitution = _substitution(graph, state)
    candidate = _candidate(graph, state, (substitution, substitution), (_skip_action(),))

    with pytest.raises(GraphValuePublicationError, match=r"source.*duplicate"):
        admit_resume_candidates((candidate,), ScopedFrameIndex())

    publication = ConfirmedPublication(
        substitution.coordinate,
        substitution.frame,
        state.revision,
        ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("confirmed"))),
    )
    candidate = replace(candidate, substitutions=(substitution,))
    with pytest.raises(GraphValuePublicationError, match="confirmed publication"):
        admit_resume_candidates((candidate,), ScopedFrameIndex(publications=(publication,)))


def test_resume_admission_rejects_substitution_coordinate_claimed_by_another_scope() -> None:
    graph = topology("source")
    state = _failed_state(running_state(frontier=("source",)))
    substitution = _substitution(graph, state)
    first = _candidate(graph, state, (substitution,), (_skip_action(),))
    second = replace(first, scope_run=replace(first.scope_run, scope=(GraphNodeId("child"),)))

    with pytest.raises(SnapshotMismatchError, match="evidence does not match"):
        admit_resume_candidates((first, second), ScopedFrameIndex())


def test_resume_admission_rejects_candidate_states_from_another_scoped_run() -> None:
    graph = topology("source")
    state = running_state(frontier=("source",))
    scope_run = root_scope_run(GraphRunId("other-run"))
    command = ResumeGraphNodes(state.revision, (_skip_action(),))
    candidate = ScopedResumeCandidate(graph, scope_run, state, state, (), command)

    with pytest.raises(SnapshotMismatchError, match="states do not match"):
        admit_resume_candidates((candidate,), ScopedFrameIndex())


def test_resume_admission_rejects_a_successor_not_produced_by_its_exact_command() -> None:
    graph = topology("source")
    state = running_state(frontier=("source",))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (GraphFrontierNode(GraphNodeId("source"), FailedGraphNode(GraphFailure("failed"))),)
        ),
    )
    command = ResumeGraphNodes(state.revision, (_skip_action(),))
    candidate = ScopedResumeCandidate(
        graph,
        root_scope_run(state.run_id),
        state,
        replace(
            _skipped_successor(state),
            join_progress=(
                GraphJoinProgress((GraphNodeId("source"),), GraphNodeId("source"), frozenset({GraphNodeId("source")})),
            ),
        ),
        (_substitution(graph, state),),
        command,
    )

    with pytest.raises(SnapshotMismatchError, match="exact command reduction"):
        admit_resume_candidates((candidate,), ScopedFrameIndex())


@pytest.mark.parametrize("tamper", ["node", "descriptor", "provenance", "settlement", "action"])
def test_resume_admission_rejects_incomplete_substitution_evidence_before_commit(tamper: str) -> None:
    graph = topology("other", "source", entries=("other", "source"))
    state = _failed_state(running_state(frontier=("source",)))
    substitution = _substitution(graph, state)
    actions = (_skip_action(),)
    if tamper == "node":
        substitution = replace(
            substitution,
            coordinate=replace(
                substitution.coordinate,
                activation=StableActivation(
                    substitution.coordinate.activation.scope_run,
                    substitution.coordinate.activation.superstep,
                    GraphNodeId("missing"),
                ),
            ),
        )
    elif tamper == "descriptor":
        substitution = replace(
            substitution,
            coordinate=replace(
                substitution.coordinate,
                descriptor=graph.publications[GraphNodeId("other")].descriptor.identity,
            ),
        )
    elif tamper == "provenance":
        substitution = replace(
            substitution,
            provenance=ExecutionPublicationProvenance(
                GraphExecutionToken(1, GraphExecutionAttemptId("forged-execution"))
            ),
        )  # type: ignore[arg-type]
    elif tamper == "settlement":
        substitution = replace(substitution, expected_revision=substitution.expected_revision + 1)
    else:
        substitution = replace(
            substitution,
            coordinate=replace(
                substitution.coordinate,
                activation=replace(substitution.coordinate.activation, node_id=GraphNodeId("other")),
            ),
        )
    candidate = _candidate(graph, state, (substitution,), actions)

    with pytest.raises(SnapshotMismatchError, match=r"unknown publication node|evidence does not match"):
        admit_resume_candidates((candidate,), ScopedFrameIndex())


def test_resume_admission_keeps_distinct_scope_coordinates_isolated() -> None:
    graph = topology("source")
    root_state = _failed_state(running_state(frontier=("source",), run_id="root"))
    root_candidate = _candidate(graph, root_state, (_substitution(graph, root_state),), (_skip_action(),))
    child_scope = replace(root_candidate.scope_run, scope=(GraphNodeId("child"),), graph_run_id=GraphRunId("child-run"))
    child_state = replace(root_state, run_id=child_scope.graph_run_id)
    child_substitution = replace(
        _substitution(graph, child_state),
        coordinate=replace(
            _substitution(graph, child_state).coordinate,
            activation=StableActivation(child_scope, child_state.superstep, GraphNodeId("source")),
        ),
    )
    child_candidate = _candidate(graph, child_state, (child_substitution,), (_skip_action(),), scope_run=child_scope)

    availability = admit_resume_candidates((root_candidate, child_candidate), ScopedFrameIndex())

    assert availability.has_publication(root_candidate.substitutions[0].coordinate)
    assert availability.has_publication(child_substitution.coordinate)


def test_resume_admission_keeps_repeated_superstep_coordinates_isolated() -> None:
    graph = topology("source")
    first_state = _failed_state(running_state(frontier=("source",), superstep=0, revision=1))
    second_state = _failed_state(running_state(frontier=("source",), superstep=1, revision=2))
    first = _substitution(graph, first_state)
    second = _substitution(graph, second_state)
    availability = admit_resume_candidates(
        (
            _candidate(graph, first_state, (first,), (_skip_action(),)),
            _candidate(graph, second_state, (second,), (_skip_action(),)),
        ),
        ScopedFrameIndex(),
    )

    assert first.coordinate != second.coordinate
    assert availability.has_publication(first.coordinate)
    assert availability.has_publication(second.coordinate)


def test_recovery_availability_rejects_a_confirmed_candidate_publication_collision() -> None:
    from mote_kernel.execution.engine.recovery import RecoveryAvailabilityCoordinates
    from mote_kernel.execution.run_context import CandidateFrameAvailability

    graph = topology("source")
    state = running_state(frontier=("source",))
    substitution = _substitution(graph, state)
    publication = ConfirmedPublication(
        substitution.coordinate,
        substitution.frame,
        state.revision,
        ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("confirmed"))),
    )

    with pytest.raises(SnapshotMismatchError, match="must be unique"):
        RecoveryAvailabilityCoordinates[str].from_frames(
            CandidateFrameAvailability(ScopedFrameIndex(publications=(publication,)), (substitution,))
        )


def test_non_skip_resume_admission_rejects_unavailable_control_target() -> None:
    graph = topology("source", "target", edges=(direct("source", "target"),))
    state = _failed_state(running_state(frontier=("source",)))
    action = _skip_action()
    command = ResumeGraphNodes(state.revision, (action,))
    successor = reduce_graph_run(state, command)
    candidate = ScopedResumeCandidate(
        graph,
        root_scope_run(state.run_id),
        state,
        successor,
        (),
        command,
    )

    with pytest.raises(GraphValueUnavailableError, match="required nodes"):
        admit_resume_candidates((candidate,), ScopedFrameIndex())


def test_resume_admission_rejects_triggered_data_target_with_an_unavailable_input() -> None:
    graph = _data_graph(target_uses_graph_input=True)
    state = _failed_state(running_state(frontier=("source",)))
    substitution = _substitution(graph, state)
    candidate = _candidate(graph, state, (substitution,), (_skip_action(),))

    with pytest.raises(GraphValueUnavailableError, match=r"required nodes.*target"):
        admit_resume_candidates((candidate,), ScopedFrameIndex())


def test_resume_admission_accepts_triggered_data_target_with_complete_inputs() -> None:
    graph = _data_graph(target_uses_graph_input=True)
    state = _failed_state(running_state(frontier=("source",)))
    substitution = _substitution(graph, state)
    scope_run = root_scope_run(state.run_id)
    graph_input = AdmittedGraphInput(
        GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
        admit_graph_input(graph, Graph.values(required="available")),
    )

    admitted = admit_resume_candidates(
        (_candidate(graph, state, (substitution,), (_skip_action(),)),),
        ScopedFrameIndex(graph_inputs=(graph_input,)),
    )

    assert admitted.has_publication(substitution.coordinate)


def test_resume_admission_join_targets_are_required_only_after_completion() -> None:
    graph = topology(
        "a",
        "b",
        "target",
        edges=(join(("a", "b"), "target"),),
        entries=("a", "b"),
    )
    state = _failed_state(running_state(frontier=("a",), run_id="join"), "a")
    action_a = _skip_action("a")
    command_a = ResumeGraphNodes(state.revision, (action_a,))
    settled_a = reduce_graph_run(state, command_a)
    scope_run = root_scope_run(state.run_id)

    admit_resume_candidates(
        (ScopedResumeCandidate(graph, scope_run, state, settled_a, (), command_a),),
        ScopedFrameIndex(),
    )

    both = replace(
        state,
        frontier=GraphFrontierState(
            tuple(
                GraphFrontierNode(GraphNodeId(node_id), FailedGraphNode(GraphFailure("failed")))
                for node_id in ("a", "b")
            )
        ),
    )
    actions = (_skip_action("a"), _skip_action("b"))
    command = ResumeGraphNodes(both.revision, actions)
    completed = reduce_graph_run(both, command)
    with pytest.raises(GraphValueUnavailableError, match=r"required nodes.*target"):
        admit_resume_candidates(
            (ScopedResumeCandidate(graph, scope_run, both, completed, (), command),),
            ScopedFrameIndex(),
        )

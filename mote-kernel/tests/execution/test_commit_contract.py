from dataclasses import replace
from typing import Protocol, cast

import pytest
from tests.execution.engine.factories import compiled_graph, node_output, running_state, task_success

import mote_kernel.execution.commit as commit_module
from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.task import GraphTask, task_identity
from mote_kernel.execution.errors import (
    FrameInstallationInvariantError,
    GraphValidationError,
    NodeExecutionContractError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.ports import FeedbackInputBinding, NodeOutputRef
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import ScopeRunCoordinate, root_scope_run
from mote_kernel.execution.result import (
    GraphCommitResult,
    TaskFailure,
    TaskSuccess,
    _commit_result,
)
from mote_kernel.execution.run_context import (
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    GraphInputEvidence,
    GraphPublicationEvidence,
    PublicationAvailabilityCoordinate,
)
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphNodeId,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    SettleGraphNode,
    SucceededGraphNodeOutcome,
    reduce_graph_run,
)


def _graph() -> CompiledGraph[str]:
    return compiled_graph("a")


class _CommitPrivateView(Protocol):
    """Typed test-side observation of the commit owner's private seal."""

    _TRANSITION_SEAL: object
    _TransitionSeal: type[object]

    @staticmethod
    def transition_seal(module: object) -> object:
        return cast(_CommitPrivateView, module)._TRANSITION_SEAL

    @staticmethod
    def transition_seal_type(module: object) -> type[object]:
        return cast(_CommitPrivateView, module)._TransitionSeal


class _TransitionConstructor(Protocol):
    def __call__(
        self,
        *,
        scope: tuple[str, ...],
        previous_state: GraphRunState | None,
        command: GraphRunCommand,
        candidate_state: GraphRunState,
        writes: commit_module.GraphCommitWriteSet[str],
        _seal: object,
    ) -> commit_module.GraphTransition[str]: ...


def _start_transition() -> commit_module.GraphTransition[str]:
    graph = _graph()
    scope_run = root_scope_run(GraphRunId("run"))
    command = project_start_graph_command(graph, scope_run.graph_run_id)
    return commit_module.prepare_transition(
        scope_run,
        None,
        command,
        None,
        graph=graph,
        graph_input=admit_graph_input(graph, Graph.values(value="input")),
    )


def _claimed_state() -> GraphRunState:
    state = running_state()
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("attempt"), None),
    )
    assert claimed.execution is not None
    return claimed


def _settlement_parts(
    *,
    success: bool = True,
) -> tuple[
    CompiledGraph[str],
    GraphRunState,
    SettleGraphNode,
    TaskSuccess[str] | TaskFailure,
]:
    graph = _graph()
    claimed = _claimed_state()
    assert claimed.execution is not None
    task = GraphTask(
        task_identity(claimed.run_id, claimed.superstep, GraphNodeId("a")),
        claimed.run_id,
        claimed.superstep,
        GraphNodeId("a"),
    )
    if success:
        outcome = SucceededGraphNodeOutcome(GraphNodeId("a"), ContinueGraphRouting())
        result: TaskSuccess[str] | TaskFailure = task_success(task, "output")
    else:
        outcome = FailedGraphNodeOutcome(GraphNodeId("a"), GraphFailure("failed"))
        result = TaskFailure(task, "failed")
    return (
        graph,
        claimed,
        SettleGraphNode(claimed.revision, claimed.execution.token, outcome),
        result,
    )


def _input_evidence(
    graph: CompiledGraph[str] | None = None,
    scope_run: ScopeRunCoordinate | None = None,
) -> GraphInputEvidence[str]:
    admitted_graph = _graph() if graph is None else graph
    coordinate = root_scope_run(GraphRunId("run")) if scope_run is None else scope_run
    return GraphInputEvidence(
        GraphInputAvailabilityCoordinate(coordinate, admitted_graph.graph_input_descriptor.identity),
        admit_graph_input(admitted_graph, Graph.values(value="input")),
    )


def _forge_transition(
    base: commit_module.GraphTransition[str],
    *,
    writes: commit_module.GraphCommitWriteSet[str],
    seal: object | None = None,
) -> commit_module.GraphTransition[str]:
    transition_seal = _CommitPrivateView.transition_seal(commit_module) if seal is None else seal
    constructor = cast(_TransitionConstructor, commit_module.GraphTransition)
    return constructor(
        scope=base.scope,
        previous_state=base.previous_state,
        command=base.command,
        candidate_state=base.candidate_state,
        writes=writes,
        _seal=transition_seal,
    )


@pytest.mark.parametrize(
    ("run_id", "revision"),
    [
        (cast(GraphRunId, " "), 0),
        (GraphRunId("run"), -1),
        (GraphRunId("run"), True),
    ],
)
def test_commit_key_requires_canonical_identity_and_revision(run_id: GraphRunId, revision: int) -> None:
    with pytest.raises(SnapshotMismatchError, match="commit key"):
        commit_module.GraphCommitKey(run_id, revision)


def test_write_set_rejects_noncanonical_members() -> None:
    with pytest.raises(SnapshotMismatchError, match="invalid commit key"):
        commit_module.GraphCommitWriteSet(commit_key=cast(commit_module.GraphCommitKey, object()))

    key = commit_module.GraphCommitKey(GraphRunId("run"), 0)
    with pytest.raises(SnapshotMismatchError, match="typed immutable tuple"):
        commit_module.GraphCommitWriteSet(
            commit_key=key,
            graph_inputs=cast(tuple[GraphInputEvidence[str], ...], []),
        )
    with pytest.raises(SnapshotMismatchError, match="typed immutable tuple"):
        commit_module.GraphCommitWriteSet(
            commit_key=key,
            graph_inputs=(cast(GraphInputEvidence[str], object()),),
        )

    graph = _graph()
    root = root_scope_run(GraphRunId("run"))
    child = ScopeRunCoordinate((GraphNodeId("child"),), GraphRunId("run"))
    root_evidence = _input_evidence(graph, root)
    child_evidence = _input_evidence(graph, child)
    with pytest.raises(SnapshotMismatchError, match="canonical and distinct"):
        commit_module.GraphCommitWriteSet(commit_key=key, graph_inputs=(root_evidence, root_evidence))
    with pytest.raises(SnapshotMismatchError, match="canonical and distinct"):
        commit_module.GraphCommitWriteSet(commit_key=key, graph_inputs=(child_evidence, root_evidence))

    with pytest.raises(SnapshotMismatchError, match="unsupported variant"):
        commit_module.GraphCommitWriteSet(
            commit_key=key,
            settlement=cast(GraphCommitResult[str], object()),
        )


def test_transition_seal_and_write_set_binding_are_owner_only() -> None:
    base = _start_transition()
    assert type(_CommitPrivateView.transition_seal(commit_module)) is _CommitPrivateView.transition_seal_type(
        commit_module
    )
    with pytest.raises(SnapshotMismatchError, match="execution commit owner"):
        _forge_transition(
            base,
            writes=base.writes,
            seal=object(),
        )
    with pytest.raises(SnapshotMismatchError, match="invalid commit write set"):
        _forge_transition(
            base,
            writes=cast(commit_module.GraphCommitWriteSet[str], object()),
        )

    mismatched_key = commit_module.GraphCommitKey(
        base.candidate_state.run_id,
        base.candidate_state.revision + 1,
    )
    mismatched = commit_module.GraphCommitWriteSet(
        commit_key=mismatched_key,
        graph_inputs=base.writes.graph_inputs,
    )
    with pytest.raises(SnapshotMismatchError, match="bound to the candidate"):
        _forge_transition(base, writes=mismatched)


def test_transition_seal_enforces_command_specific_write_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    start = _start_transition()
    _graph_for_settle, claimed, settle_command, settle_result = _settlement_parts()
    settle_transition = commit_module.prepare_transition(
        root_scope_run(claimed.run_id),
        claimed,
        settle_command,
        settle_result,
        graph=_graph_for_settle,
    )
    settlement = settle_transition.writes.settlement
    assert settlement is not None

    start_with_settlement = commit_module.GraphCommitWriteSet(
        commit_key=commit_module.GraphCommitKey(start.candidate_state.run_id, start.candidate_state.revision),
        graph_inputs=start.writes.graph_inputs,
        settlement=settlement,
    )
    with pytest.raises(SnapshotMismatchError, match="StartGraphRun"):
        _forge_transition(start, writes=start_with_settlement)

    start_without_input = commit_module.GraphCommitWriteSet(
        commit_key=commit_module.GraphCommitKey(start.candidate_state.run_id, start.candidate_state.revision),
    )
    with pytest.raises(SnapshotMismatchError, match="exactly one graph input"):
        _forge_transition(start, writes=start_without_input)

    state = running_state()
    claim_command = ClaimGraphExecution(state.revision, GraphExecutionAttemptId("claim"), None)
    claim_candidate = reduce_graph_run(state, claim_command)
    constructor = cast(_TransitionConstructor, commit_module.GraphTransition)
    claim_base = constructor(
        scope=(),
        previous_state=state,
        command=claim_command,
        candidate_state=claim_candidate,
        writes=commit_module.GraphCommitWriteSet(
            commit_key=commit_module.GraphCommitKey(claim_candidate.run_id, claim_candidate.revision),
        ),
        _seal=_CommitPrivateView.transition_seal(commit_module),
    )
    claim_with_input = commit_module.GraphCommitWriteSet(
        commit_key=commit_module.GraphCommitKey(claim_candidate.run_id, claim_candidate.revision),
        graph_inputs=start.writes.graph_inputs,
    )
    with pytest.raises(SnapshotMismatchError, match="only StartGraphRun"):
        _forge_transition(claim_base, writes=claim_with_input)

    settle_without_evidence = commit_module.GraphCommitWriteSet(
        commit_key=commit_module.GraphCommitKey(
            settle_transition.candidate_state.run_id,
            settle_transition.candidate_state.revision,
        ),
    )
    with pytest.raises(SnapshotMismatchError, match="does not match its command"):
        _forge_transition(settle_transition, writes=settle_without_evidence)

    original_publications = commit_module.GraphCommitWriteSet.publications
    monkeypatch.setattr(commit_module.GraphCommitWriteSet, "publications", property(lambda _self: ()))
    with pytest.raises(SnapshotMismatchError, match="exactly one publication"):
        _forge_transition(settle_transition, writes=settle_transition.writes)
    monkeypatch.setattr(commit_module.GraphCommitWriteSet, "publications", original_publications)

    _failure_graph, failed_claimed, failed_command, failed_result = _settlement_parts(success=False)
    failed_transition = commit_module.prepare_transition(
        root_scope_run(failed_claimed.run_id),
        failed_claimed,
        failed_command,
        failed_result,
        graph=_failure_graph,
    )
    success_publications = settle_transition.writes.publications
    failed_publications = property(lambda _self: success_publications)
    monkeypatch.setattr(commit_module.GraphCommitWriteSet, "publications", failed_publications)
    with pytest.raises(SnapshotMismatchError, match="cannot publish"):
        _forge_transition(failed_transition, writes=failed_transition.writes)

    non_settle_with_settlement = commit_module.GraphCommitWriteSet(
        commit_key=commit_module.GraphCommitKey(claim_candidate.run_id, claim_candidate.revision),
        settlement=settlement,
    )
    monkeypatch.setattr(commit_module.GraphCommitWriteSet, "publications", original_publications)
    with pytest.raises(SnapshotMismatchError, match="only SettleGraphNode"):
        _forge_transition(claim_base, writes=non_settle_with_settlement)


def test_prepare_transition_requires_matching_frame_evidence() -> None:
    graph = _graph()
    scope_run = root_scope_run(GraphRunId("run"))
    start_command = project_start_graph_command(graph, scope_run.graph_run_id)
    with pytest.raises(FrameInstallationInvariantError, match="graph input evidence"):
        commit_module.prepare_transition(scope_run, None, start_command, None, graph=graph)

    state = running_state()
    claim_command = ClaimGraphExecution(state.revision, GraphExecutionAttemptId("claim"), None)
    with pytest.raises(FrameInstallationInvariantError, match="only StartGraphRun"):
        commit_module.prepare_transition(
            scope_run,
            state,
            claim_command,
            None,
            graph=graph,
            graph_input=admit_graph_input(graph, Graph.values(value="input")),
        )

    claimed = reduce_graph_run(state, claim_command)
    assert claimed.execution is not None
    settle_command = SettleGraphNode(
        claimed.revision,
        claimed.execution.token,
        SucceededGraphNodeOutcome(GraphNodeId("a"), ContinueGraphRouting()),
    )
    with pytest.raises(FrameInstallationInvariantError, match="settlement evidence"):
        commit_module.prepare_transition(scope_run, claimed, settle_command, None, graph=graph)

    task = GraphTask(
        task_identity(claimed.run_id, claimed.superstep, GraphNodeId("wrong")),
        claimed.run_id,
        claimed.superstep,
        GraphNodeId("wrong"),
    )
    with pytest.raises(SnapshotMismatchError, match="settlement result"):
        commit_module.prepare_transition(
            scope_run,
            claimed,
            settle_command,
            TaskFailure(task, "wrong node"),
            graph=graph,
        )

    with pytest.raises(FrameInstallationInvariantError, match="only SettleGraphNode"):
        commit_module.prepare_transition(
            scope_run,
            state,
            claim_command,
            TaskFailure(task, "wrong command"),
            graph=graph,
        )


def test_commit_result_rejects_incomplete_publication_contract() -> None:
    graph, claimed, command, result = _settlement_parts()
    transition = commit_module.prepare_transition(
        root_scope_run(claimed.run_id),
        claimed,
        command,
        result,
        graph=graph,
    )
    publication = transition.writes.publications[0]
    assert isinstance(result, TaskSuccess)
    with pytest.raises(NodeExecutionContractError, match="requires publication"):
        _commit_result(result, None)
    with pytest.raises(NodeExecutionContractError, match="must carry the task output"):
        _commit_result(result, replace(publication, frame=node_output("other")))
    with pytest.raises(NodeExecutionContractError, match="cannot publish"):
        _commit_result(TaskFailure(result.task, "failed"), publication)


def test_frame_evidence_requires_exact_typed_frames_and_is_not_hashable() -> None:
    graph, claimed, command, result = _settlement_parts()
    transition = commit_module.prepare_transition(
        root_scope_run(claimed.run_id),
        claimed,
        command,
        result,
        graph=graph,
    )
    publication = transition.writes.publications[0]
    input_coordinate: GraphInputAvailabilityCoordinate[str] = GraphInputAvailabilityCoordinate(
        root_scope_run(GraphRunId("run")),
        graph.graph_input_descriptor.identity,
    )
    input_frame = admit_graph_input(graph, Graph.values(value="input"))
    with pytest.raises(SnapshotMismatchError, match="graph input evidence"):
        GraphInputEvidence(cast(GraphInputAvailabilityCoordinate[str], object()), input_frame)
    input_evidence = GraphInputEvidence(input_coordinate, input_frame)
    with pytest.raises(TypeError, match="unhashable"):
        hash(input_evidence)

    with pytest.raises(SnapshotMismatchError, match="publication evidence"):
        GraphPublicationEvidence(
            cast(PublicationAvailabilityCoordinate[str], object()),
            publication.frame,
            publication.provenance,
        )
    valid_publication = GraphPublicationEvidence(
        publication.coordinate,
        publication.frame,
        ExecutionPublicationProvenance(publication.provenance.execution_token),
    )
    with pytest.raises(TypeError, match="unhashable"):
        hash(valid_publication)


def test_feedback_binding_rejects_a_non_node_output_repeat_reference() -> None:
    with pytest.raises(GraphValidationError, match="feedback repeat"):
        FeedbackInputBinding(
            Graph.graph_input("value", str),
            cast(NodeOutputRef, object()),
        )

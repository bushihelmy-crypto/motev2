# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.engine.factories import compiled_graph, direct, leased_state, running_state

import mote_kernel.execution.family_driver as family_driver
from mote_kernel.execution import Graph
from mote_kernel.execution.cancellation import wait_for_owner_task
from mote_kernel.execution.engine.admission import admit_graph_input
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import FrameInstallationInvariantError, ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.family_driver import (
    GraphCommit,
    GraphTransition,
    _child_failure_reason,
    _ChildHandle,
    _ChildPhase,
    _evidence_adapter,
    _EvidencePublisher,
    _EvidenceReader,
    _frames_for_owner,
    _GraphRun,
    _opaque_handle,
    admit_continued_root,
    commit_transition,
    scoped_commit,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import GraphOutputView, NamedValue, _make_graph_output_view
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.invocation import PlannedFence, PlannedResume, lineage_states, plan_fences
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    AbortedChild,
    AbortedGraph,
    ActiveChild,
    AwaitingResume,
    CompletedChild,
    CompletedGraph,
    FailedChild,
    FailedGraph,
    MissingChild,
    TaskFailure,
    WaitingForChildren,
)
from mote_kernel.execution.run_context import (
    ChildBoundaryAvailabilityCoordinate,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ScopedFrameIndex,
    _CompiledFamilyIdentity,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNodeOutcome,
    GraphAbortReason,
    GraphActivationIdentity,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphFrontierState,
    GraphNodeId,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    ResumeGraphNodes,
    SettleGraphNode,
    SucceededGraphNodeOutcome,
    reduce_graph_run,
)


async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(value="output")


def encode_empty(_value: Graph.Values[str]) -> bytes:
    return b""


def decode_empty(_payload: bytes) -> Graph.Values[str]:
    return Graph.values()


def resume_empty_interrupt(
    graph: Graph[str],
    awaiting: Graph.AwaitingResumeResult[str],
    node_id: str,
    *,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[str]:
    interrupt = next(view for view in awaiting.interrupts if view.scope == scope and view.node_id == node_id)
    return graph.resume_interrupted(
        node_id,
        interrupt.interrupt_id,
        Graph.values(),
        scope=scope,
    )


def new_evidence_publisher() -> _EvidencePublisher[str]:
    publisher, _reader = _evidence_adapter((), ScopedFrameIndex())
    return publisher


def nested_graph() -> CompiledGraph[str]:
    child = Graph[str]("ownership.child")
    child.add_node("leaf", produce, inputs={}, outputs={"value": str})
    child.set_outputs({"value": Graph.node_output("leaf", "value")})
    parent = Graph[str]("ownership.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    return parent._compile().graph


def root_owner(
    graph: CompiledGraph[str],
    state: GraphRunState,
    *,
    frames: ScopedFrameIndex[str] | None = None,
    commit: GraphCommit[str] | None = None,
) -> _GraphRun[str]:
    scope_run = root_scope_run(state.run_id)
    return graph_owner(graph, scope_run, state, frames=frames, commit=commit)


def graph_owner(
    graph: CompiledGraph[str],
    scope_run: ScopeRunCoordinate,
    state: GraphRunState,
    *,
    frames: ScopedFrameIndex[str] | None = None,
    commit: GraphCommit[str] | None = None,
    position: tuple[int, ...] = (),
    parent_activation: StableActivation | None = None,
    publisher: _EvidencePublisher[str] | None = None,
) -> _GraphRun[str]:
    limits = ExecutionLimits()
    evidence_publisher = new_evidence_publisher() if publisher is None else publisher
    return _GraphRun(
        graph,
        scope_run,
        state,
        ScopedFrameIndex() if frames is None else frames,
        limits,
        scoped_commit(scope_run, commit),
        family_driver._make_child_constructor(scope_run, limits, commit, evidence_publisher),
        position,
        parent_activation,
        evidence_publisher,
    )


def fail_owner_construction(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    *,
    scope_depth: int,
) -> None:
    original = family_driver.require_scoped_snapshot_matches_graph

    def reject(
        graph: CompiledGraph[str],
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
    ) -> None:
        if len(scope_run.scope) == scope_depth:
            raise error
        original(graph, state, scope_run)

    monkeypatch.setattr(family_driver, "require_scoped_snapshot_matches_graph", reject)


def nested_runtime() -> tuple[
    CompiledGraph[str],
    GraphRunState,
    _GraphRun[str],
    GraphActivationIdentity,
    ScopeRunCoordinate,
    StableActivation,
    GraphRunState,
]:
    graph = nested_graph()
    state = running_state(
        definition_id=graph.definition_id,
        version=graph.version,
        frontier=("nested",),
        run_id="parent",
    )
    owner = root_owner(graph, state)
    parent = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("nested"))
    scope_run = root_scope_run(state.run_id)
    child_scope = child_scope_run_for_activation(scope_run, parent)
    child_graph = graph.nested_graphs[parent.node_id]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, parent),
    )
    activation = StableActivation(scope_run, parent.superstep, parent.node_id)
    return graph, state, owner, parent, child_scope, activation, child_state


def graph_output(graph: CompiledGraph[str], value: str) -> GraphOutputView[str]:
    return _make_graph_output_view(
        (NamedValue("value", value),),
        graph.graph_output_descriptor.declarations,
    )


async def admit_continuation_root(
    graph: CompiledGraph[str],
    state: GraphRunState,
    bindings: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[str] | None = None,
    commit: GraphCommit[str] | None = None,
    fences: tuple[PlannedFence, ...] = (),
) -> tuple[_GraphRun[str], _EvidenceReader[str]]:
    return await admit_continued_root(
        graph,
        state,
        bindings,
        ScopedFrameIndex() if frames is None else frames,
        ExecutionLimits(),
        commit,
        fences,
        (),
        _CompiledFamilyIdentity(),
        recovered=True,
    )


@pytest.mark.asyncio
async def test_scoped_commit_rejects_a_transition_for_another_owner() -> None:
    graph = nested_graph()
    scope_run = root_scope_run(GraphRunId("run"))
    transitions: list[GraphTransition[str]] = []

    async def capture(transition: GraphTransition[str], /) -> GraphRunState:
        transitions.append(transition)
        return transition.candidate_state

    await commit_transition(
        scope_run,
        None,
        project_start_graph_command(graph, scope_run.graph_run_id),
        None,
        capture,
        graph=graph,
    )
    foreign = ScopeRunCoordinate((GraphNodeId("foreign"),), scope_run.graph_run_id)

    with pytest.raises(SnapshotMismatchError, match="different scoped graph run"):
        await scoped_commit(foreign, None)(transitions[0])


@pytest.mark.asyncio
async def test_resume_transition_rejects_an_unadmitted_successor_before_commit() -> None:
    state = running_state()
    scope_run = root_scope_run(state.run_id)
    commits: list[GraphTransition[str]] = []

    async def capture(transition: GraphTransition[str], /) -> GraphRunState:
        commits.append(transition)
        return transition.candidate_state

    with pytest.raises(FrameInstallationInvariantError, match="admitted successor"):
        await commit_transition(
            scope_run,
            state,
            AbortGraphRun(state.revision, GraphAbortReason("abort")),
            None,
            capture,
            graph=compiled_graph("a"),
            admitted_successor=state,
        )

    assert commits == []


@pytest.mark.asyncio
async def test_owner_transition_rejects_a_candidate_that_fails_frontier_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = nested_graph()
    state = running_state(
        definition_id=graph.definition_id,
        version=graph.version,
        frontier=("nested",),
    )
    commits: list[GraphTransition[str]] = []

    async def capture(transition: GraphTransition[str], /) -> GraphRunState:
        commits.append(transition)
        return transition.candidate_state

    owner = root_owner(graph, state, commit=capture)

    def reject_transition_admission(
        _graph: CompiledGraph[str],
        _previous_state: GraphRunState | None,
        _command: GraphRunCommand,
        _candidate_state: GraphRunState,
    ) -> str:
        return "admission failed"

    monkeypatch.setattr(family_driver, "transition_admission_error", reject_transition_admission)

    with pytest.raises(SnapshotMismatchError, match="admission failed"):
        await owner._transition(AbortGraphRun(state.revision, GraphAbortReason("abort")))

    assert commits == []


@pytest.mark.asyncio
async def test_commit_boundary_rejects_a_forged_completion_that_discards_a_successor() -> None:
    graph = compiled_graph("a", "b", edges=(direct("a", "b"),))
    state = running_state()
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("completion-forgery"), None),
    )
    assert claimed.execution is not None
    settled = reduce_graph_run(
        claimed,
        SettleGraphNode(
            claimed.revision,
            claimed.execution.token,
            SucceededGraphNodeOutcome(GraphNodeId("a"), ContinueGraphRouting()),
        ),
    )
    commits: list[GraphTransition[str]] = []

    async def capture(transition: GraphTransition[str], /) -> GraphRunState:
        commits.append(transition)
        return transition.candidate_state

    with pytest.raises(SnapshotMismatchError, match="discarded a compiled successor"):
        await commit_transition(
            root_scope_run(settled.run_id),
            settled,
            CompleteGraphFrontier(settled.revision),
            None,
            capture,
            graph=graph,
        )

    assert commits == []


@pytest.mark.asyncio
async def test_owner_task_wait_preserves_an_inner_cancellation() -> None:
    async def cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await wait_for_owner_task(asyncio.create_task(cancel()))


def test_frame_partition_requires_known_children() -> None:
    root = root_scope_run(GraphRunId("root"))
    child = ScopeRunCoordinate((GraphNodeId("child"),), GraphRunId("child"))
    child_activation = StableActivation(root, 0, GraphNodeId("child"))
    child_binding = ChildStateBinding(child, child_activation, running_state(run_id=child.graph_run_id))
    unknown = ScopeRunCoordinate((GraphNodeId("unknown"),), GraphRunId("unknown"))
    graph = nested_graph()
    boundary = ConfirmedChildBoundary(
        ChildBoundaryAvailabilityCoordinate(unknown, graph.graph_output_descriptor.identity),
        _make_graph_output_view((), graph.graph_output_descriptor.declarations),
    )
    with pytest.raises(SnapshotMismatchError, match="no child binding"):
        _frames_for_owner(ScopedFrameIndex(child_boundaries=(boundary,)), (), root)

    publish, read = _evidence_adapter((child_binding,), ScopedFrameIndex())
    assert read()[0] == (child_binding,)
    changed_parent = replace(
        child_binding,
        parent_activation=StableActivation(root, 1, GraphNodeId("child")),
    )
    with pytest.raises(SnapshotMismatchError, match="changed its parent activation"):
        publish(changed_parent, ScopedFrameIndex())


@pytest.mark.asyncio
async def test_graph_owner_rejects_foreign_scope_and_duplicate_or_unknown_positions() -> None:
    graph, state, owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    with pytest.raises(SnapshotMismatchError, match="scope-run coordinate"):
        graph_owner(
            graph,
            ScopeRunCoordinate((GraphNodeId("foreign"),), state.run_id),
            state,
        )

    position = owner.child_position(parent)
    with pytest.raises(ResultCollectionError, match="position does not match"):
        owner.accept_child_call((*position, 99), parent, ActiveChild(parent), None)
    owner._children.append((position, parent, ActiveChild(parent), None))
    with pytest.raises(ResultCollectionError, match="more than one child call"):
        owner.child_position(parent)

    unknown = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("unknown"))
    with pytest.raises(ResultCollectionError, match="not part of the parent definition"):
        owner.child_position(unknown)

    foreign_parent = replace(parent, run_id=GraphRunId("foreign-parent"))
    child_graph = graph.nested_graphs[parent.node_id]
    with pytest.raises(SnapshotMismatchError, match="parent activation does not belong"):
        await owner._child_constructor(
            foreign_parent,
            child_graph,
            admit_graph_input(child_graph, Graph.values()),
            position,
        )
    with pytest.raises(SnapshotMismatchError, match="construction does not match"):
        await owner._child_constructor(
            parent,
            graph,
            admit_graph_input(graph, Graph.values()),
            position,
        )


def test_child_admits_its_terminal_boundary_before_parent_installation() -> None:
    graph, state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    child_graph = graph.nested_graphs[parent.node_id]
    output = graph_output(child_graph, "output")
    terminal = CompletedChild(parent, output)
    child_owner = graph_owner(
        child_graph,
        child_scope,
        replace(child_state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(())),
        position=(0, 0),
        parent_activation=activation,
    )
    boundary = child_owner.terminal_boundary(parent, terminal)
    assert boundary is not None
    assert boundary.coordinate.child_scope_run == child_scope

    foreign_parent = replace(parent, run_id=GraphRunId("foreign-parent"))
    with pytest.raises(SnapshotMismatchError, match="parent activation"):
        child_owner.terminal_boundary(foreign_parent, terminal)

    aborted_owner = root_owner(graph, state)
    aborted_owner._children.append(((0, 0), parent, AbortedChild(parent, GraphAbortReason("aborted")), None))
    with pytest.raises(ResultCollectionError, match="aborted child"):
        aborted_owner._install_terminal(0, boundary)

    missing_owner = root_owner(graph, state)
    missing_owner._children.append(((0, 0), parent, CompletedChild(parent, output), None))
    with pytest.raises(ResultCollectionError, match="exact output boundary"):
        missing_owner._install_terminal(0, None)

    mismatch_owner = root_owner(graph, state)
    mismatch_owner._children.append(((0, 0), parent, CompletedChild(parent, output), None))
    other = graph_output(child_graph, "other")
    with pytest.raises(ResultCollectionError, match="exact output boundary"):
        mismatch_owner._install_terminal(0, replace(boundary, frame=other))

    parent_owner = root_owner(graph, state)
    parent_owner._children.append(((0, 0), parent, CompletedChild(parent, output), None))
    parent_owner._install_terminal(0, boundary)
    assert parent_owner._frames.child_boundaries == (boundary,)


@pytest.mark.asyncio
async def test_child_start_rejects_a_stale_parent_activation() -> None:
    _graph, state, owner, _parent, _child_scope, _activation, _child_state = nested_runtime()
    stale = GraphActivationIdentity(state.run_id, state.superstep + 1, GraphNodeId("nested"))

    with pytest.raises(ResultCollectionError, match="stale or foreign"):
        await owner._start_child(MissingChild(stale))


@pytest.mark.asyncio
async def test_child_start_hands_off_a_confirmed_owner_before_rethrowing_cancellation() -> None:
    graph, state, _owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    write_finished = asyncio.Event()
    acknowledge = asyncio.Event()

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if transition.previous_state is None:
            write_finished.set()
            await acknowledge.wait()
        return transition.candidate_state

    owner = root_owner(graph, state, commit=commit)
    task = asyncio.create_task(owner._start_child(MissingChild(parent)))
    await write_finished.wait()
    task.cancel()
    await asyncio.sleep(0)
    acknowledge.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(owner._children) == 1
    _position, recorded_parent, phase, handle = owner._children[0]
    assert recorded_parent == parent
    assert isinstance(phase, ActiveChild)
    assert isinstance(handle, _ChildHandle)
    await owner.abort(GraphAbortReason("cancelled"))
    await owner.release()


@pytest.mark.asyncio
async def test_fresh_child_handoff_failure_cleans_the_unhanded_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, state, _owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    handles: list[_ChildHandle[str]] = []
    transitions: list[GraphTransition[str]] = []

    class HandoffError(RuntimeError):
        pass

    original = HandoffError("child handle handoff failed")

    def reject_handoff(
        _owner: _GraphRun[str],
        _position: tuple[int, ...],
        _parent: GraphActivationIdentity,
        _phase: _ChildPhase[str],
        handle: _ChildHandle[str] | None,
    ) -> None:
        assert handle is not None
        handles.append(handle)
        raise original

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        transitions.append(transition)
        return transition.candidate_state

    monkeypatch.setattr(_GraphRun, "accept_child_call", reject_handoff)
    owner = root_owner(graph, state, commit=commit)

    with pytest.raises(HandoffError) as raised:
        await owner._start_child(MissingChild(parent))

    assert raised.value is original
    assert owner._children == []
    assert tuple(transition.scope for transition in transitions if isinstance(transition.command, AbortGraphRun)) == (
        ("nested",),
    )
    assert len(handles) == 1
    with pytest.raises(ResultCollectionError, match="already released"):
        await handles[0].drive()


@pytest.mark.asyncio
async def test_child_drive_rejects_inconsistent_terminal_projections() -> None:
    graph, state, _owner, parent, child_scope, _activation, _child_state = nested_runtime()
    child_graph = graph.nested_graphs[parent.node_id]
    output = graph_output(child_graph, "output")
    boundary = ConfirmedChildBoundary(
        ChildBoundaryAvailabilityCoordinate(child_scope, child_graph.graph_output_descriptor.identity),
        output,
    )

    async def no_abort(_reason: GraphAbortReason) -> None:
        return None

    async def no_release() -> None:
        return None

    invalid_owner = root_owner(graph, state)
    invalid_owner._children.append(((0, 0), parent, AwaitingResume(()), None))
    await invalid_owner._drive_child(0)

    async def awaiting_terminal() -> tuple[AwaitingResume, AbortedChild, None]:
        return AwaitingResume(()), AbortedChild(parent, GraphAbortReason("aborted")), None

    awaiting_owner = root_owner(graph, state)
    awaiting_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](awaiting_terminal, no_abort, no_release),
        )
    )
    with pytest.raises(ResultCollectionError, match="awaiting child returned terminal"):
        await awaiting_owner._drive_child(0)

    async def missing_terminal() -> tuple[CompletedGraph, None, None]:
        return CompletedGraph(), None, None

    missing_owner = root_owner(graph, state)
    missing_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](missing_terminal, no_abort, no_release),
        )
    )
    with pytest.raises(ResultCollectionError, match="no terminal projection"):
        await missing_owner._drive_child(0)

    async def completed() -> tuple[CompletedGraph, AbortedChild, None]:
        return CompletedGraph(), AbortedChild(parent, GraphAbortReason("aborted")), None

    completed_owner = root_owner(graph, state)
    completed_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](completed, no_abort, no_release),
        )
    )
    with pytest.raises(ResultCollectionError, match="non-completed"):
        await completed_owner._drive_child(0)

    async def failed() -> tuple[FailedGraph, AbortedChild, None]:
        return FailedGraph(), AbortedChild(parent, GraphAbortReason("aborted")), None

    failed_owner = root_owner(graph, state)
    failed_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](failed, no_abort, no_release),
        )
    )
    with pytest.raises(ResultCollectionError, match="non-failed"):
        await failed_owner._drive_child(0)

    async def aborted() -> tuple[AbortedGraph, CompletedChild[str], ConfirmedChildBoundary[str]]:
        return AbortedGraph(), CompletedChild(parent, output), boundary

    aborted_owner = root_owner(graph, state)
    aborted_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](aborted, no_abort, no_release),
        )
    )
    with pytest.raises(ResultCollectionError, match="non-aborted"):
        await aborted_owner._drive_child(0)

    commit_cancellation = asyncio.CancelledError("child commit cancelled")

    async def cancelled_commit() -> asyncio.CancelledError:
        return commit_cancellation

    cancelled_owner = root_owner(graph, state)
    cancelled_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](cancelled_commit, no_abort, no_release),
        )
    )
    with pytest.raises(asyncio.CancelledError, match="child commit cancelled") as raised:
        await cancelled_owner._drive_child(0)
    assert raised.value is commit_cancellation
    assert cancelled_owner.consume_commit_origin_cancellation(commit_cancellation)


@pytest.mark.asyncio
async def test_awaiting_child_failure_cleanup_requires_its_live_owner_handle() -> None:
    _graph, state, owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    failed = leased_state(state)
    execution = failed.execution
    assert execution is not None
    owner._state = reduce_graph_run(
        failed,
        SettleGraphNode(
            failed.revision,
            execution.token,
            FailedGraphNodeOutcome(parent.node_id, GraphFailure("failed")),
        ),
    )
    waiting_parent = replace(parent, node_id=GraphNodeId("waiting"))
    owner._children.extend(
        (
            ((0, 0), parent, FailedChild(parent, "failed"), None),
            ((0, 1), waiting_parent, AwaitingResume(()), None),
        )
    )

    with pytest.raises(ResultCollectionError, match="no live owner handle"):
        await owner._abort_awaiting_children_after_failure()

    await owner.release()


def test_child_failure_projection_requires_diagnostics_and_preserves_all_failures() -> None:
    with pytest.raises(ResultCollectionError, match="no failed frontier node"):
        _child_failure_reason(replace(running_state(), status=GraphRunStatus.FAILED))

    failed = leased_state(running_state(frontier=("a", "b")))
    execution = failed.execution
    assert execution is not None
    for node_id, reason in (("a", "first"), ("b", "second")):
        failed = reduce_graph_run(
            failed,
            SettleGraphNode(
                failed.revision,
                execution.token,
                FailedGraphNodeOutcome(GraphNodeId(node_id), GraphFailure(reason)),
            ),
        )

    assert failed.status is GraphRunStatus.FAILED
    assert _child_failure_reason(failed) == "nested graph failed: a: first; b: second"


@pytest.mark.asyncio
async def test_nested_settlement_requires_one_terminal_child_call() -> None:
    _graph, state, owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    task = GraphTask(TaskId("nested"), state.run_id, state.superstep, parent.node_id)
    result = TaskFailure(task, "failed")

    with pytest.raises(ResultCollectionError, match="no admitted child call"):
        await owner._retire_child(result)

    owner._children.append(((0, 0), parent, ActiveChild(parent), None))
    with pytest.raises(ResultCollectionError, match="unretired terminal child"):
        await owner._retire_child(result)


def test_owner_evidence_cannot_cross_root_child_roles_or_leave_an_active_call() -> None:
    graph, _state, owner, parent, child_scope, activation, child_state = nested_runtime()
    assert "_read_evidence" not in _GraphRun.__slots__
    with pytest.raises(SnapshotMismatchError, match="root graph evidence"):
        owner.handoff_evidence()

    child_graph = graph.nested_graphs[parent.node_id]
    child_owner = graph_owner(
        child_graph,
        child_scope,
        child_state,
        position=(0, 0),
        parent_activation=activation,
    )
    with pytest.raises(SnapshotMismatchError, match="child graph evidence"):
        child_owner.freeze_root_evidence(lambda: ((), ScopedFrameIndex()))

    owner._children.append(((0, 0), parent, ActiveChild(parent), None))
    with pytest.raises(SnapshotMismatchError, match="no handed-off export evidence"):
        owner.freeze_root_evidence(lambda: ((), ScopedFrameIndex()))


@pytest.mark.asyncio
async def test_opaque_handle_is_one_shot_and_inert_after_release() -> None:
    graph, _state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    aborted_state = reduce_graph_run(
        child_state,
        AbortGraphRun(child_state.revision, GraphAbortReason("aborted")),
    )
    child_graph = graph.nested_graphs[parent.node_id]
    child_owner = graph_owner(
        child_graph,
        child_scope,
        aborted_state,
        position=(0, 0),
        parent_activation=activation,
    )
    handle = _opaque_handle(child_owner, parent)

    child_result = await handle.drive()
    assert not isinstance(child_result, asyncio.CancelledError)
    disposition, terminal, boundary = child_result
    assert isinstance(disposition, AbortedGraph)
    assert isinstance(terminal, AbortedChild)
    assert boundary is None
    with pytest.raises(ResultCollectionError, match="only be handed off once"):
        await handle.drive()

    await handle.release()
    await handle.release()
    await handle.abort(GraphAbortReason("ignored"))
    with pytest.raises(ResultCollectionError, match="already released"):
        await handle.drive()

    repeated = root_owner(graph, running_state(definition_id=graph.definition_id, frontier=("nested",)))
    await repeated.release()
    await repeated.release()


@pytest.mark.asyncio
async def test_opaque_handle_does_not_publish_a_child_whose_abort_was_not_committed() -> None:
    class AbortCommitError(RuntimeError):
        pass

    graph, _state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    child_graph = graph.nested_graphs[parent.node_id]
    original = AbortCommitError("abort commit failed")
    published: list[ChildStateBinding] = []

    async def reject_abort(transition: GraphTransition[str], /) -> GraphRunState:
        if isinstance(transition.command, AbortGraphRun):
            raise original
        return transition.candidate_state

    def publish(binding: ChildStateBinding, _frames: ScopedFrameIndex[str]) -> None:
        published.append(binding)

    child_owner = graph_owner(
        child_graph,
        child_scope,
        child_state,
        commit=reject_abort,
        position=(0, 0),
        parent_activation=activation,
        publisher=publish,
    )
    handle = _opaque_handle(child_owner, parent)

    with pytest.raises(AbortCommitError) as raised:
        await handle.abort(GraphAbortReason("abort"))

    assert raised.value is original
    assert child_owner.state.status is GraphRunStatus.RUNNING
    assert published == []
    await handle.release()


@pytest.mark.asyncio
async def test_continued_owner_projects_a_terminal_failed_child_without_reexecution() -> None:
    graph, state, _owner, _parent, child_scope, activation, child_state = nested_runtime()
    failed_child = leased_state(child_state)
    execution = failed_child.execution
    assert execution is not None
    failed_child = reduce_graph_run(
        failed_child,
        SettleGraphNode(
            failed_child.revision,
            execution.token,
            FailedGraphNodeOutcome(GraphNodeId("leaf"), GraphFailure("child failed")),
        ),
    )
    root, _evidence_reader = await admit_continuation_root(
        graph,
        state,
        (ChildStateBinding(child_scope, activation, failed_child),),
    )

    try:
        assert len(root._children) == 1
        _position, _parent, phase, handle = root._children[0]
        assert isinstance(phase, FailedChild)
        assert phase.failure == "child failed"
        assert handle is None

        disposition = await root.drive_quantum()

        assert isinstance(disposition, FailedGraph)
        assert root.state.status is GraphRunStatus.FAILED
    finally:
        await root.release()


@pytest.mark.asyncio
async def test_continued_owner_projects_a_completed_child_from_its_confirmed_boundary() -> None:
    graph, state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    child_graph = graph.nested_graphs[parent.node_id]
    claimed = leased_state(child_state)
    execution = claimed.execution
    assert execution is not None
    settled = reduce_graph_run(
        claimed,
        SettleGraphNode(
            claimed.revision,
            execution.token,
            SucceededGraphNodeOutcome(GraphNodeId("leaf"), ContinueGraphRouting()),
        ),
    )
    completed = reduce_graph_run(
        settled,
        CompleteGraphFrontier(settled.revision),
    )
    output = graph_output(child_graph, "output")
    boundary = ConfirmedChildBoundary(
        ChildBoundaryAvailabilityCoordinate(
            child_scope,
            child_graph.graph_output_descriptor.identity,
        ),
        output,
    )
    root, _evidence_reader = await admit_continuation_root(
        graph,
        state,
        (ChildStateBinding(child_scope, activation, completed),),
        ScopedFrameIndex(child_boundaries=(boundary,)),
    )

    try:
        assert len(root._children) == 1
        _position, _parent, phase, handle = root._children[0]
        assert isinstance(phase, CompletedChild)
        assert phase.output == output
        assert handle is None

        disposition = await root.drive_quantum()

        assert isinstance(disposition, CompletedGraph)
        assert root.state.status is GraphRunStatus.COMPLETED
    finally:
        await root.release()


@pytest.mark.asyncio
async def test_child_owner_completes_resume_before_opaque_handle_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Graph.interrupt(b"question")
        return Graph.success(Graph.values())

    child = Graph[str]("ownership.resume-order.child")
    child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    child.add_node("leaf", interrupt_once, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("ownership.resume-order.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    awaiting = await parent.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    events: list[tuple[str, tuple[str, ...]]] = []
    original_accept = _GraphRun[str].accept_child_call
    original_resume = _GraphRun[str].apply_admission_resume

    def record_handoff(
        self: _GraphRun[str],
        position: tuple[int, ...],
        parent_activation: GraphActivationIdentity,
        phase: _ChildPhase[str],
        handle: _ChildHandle[str] | None,
    ) -> None:
        if handle is not None:
            events.append(("handoff", (parent_activation.node_id,)))
        original_accept(self, position, parent_activation, phase, handle)

    async def record_owner_resume(
        self: _GraphRun[str],
        planned: PlannedResume[str],
    ) -> None:
        assert self.state.run_id == planned.scope_run.graph_run_id
        events.append(("owner", tuple(planned.scope_run.scope)))
        await original_resume(self, planned)

    async def record_commit(transition: GraphTransition[str], /) -> GraphRunState:
        if isinstance(transition.command, ResumeGraphNodes):
            events.append(("resume", transition.scope))
        return transition.candidate_state

    monkeypatch.setattr(_GraphRun, "accept_child_call", record_handoff)
    monkeypatch.setattr(_GraphRun, "apply_admission_resume", record_owner_resume)
    completed = await parent.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(resume_empty_interrupt(parent, awaiting, "leaf", scope=("nested",)),),
        commit=record_commit,
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert events.index(("owner", ("nested",))) < events.index(("resume", ("nested",)))
    assert events.index(("resume", ("nested",))) < events.index(("handoff", ("nested",)))


@pytest.mark.asyncio
async def test_resume_transition_is_reduced_once(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Graph.interrupt(b"question")
        return Graph.success(Graph.values())

    graph = Graph[str]("ownership.single-resume-reduction")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("leaf", interrupt_once, inputs={}, outputs={})
    graph.set_outputs({})
    awaiting = await graph.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    original_reduce = family_driver.reduce_graph_run
    resume_reductions = 0

    def count_reduce(state: GraphRunState | None, command: GraphRunCommand) -> GraphRunState:
        nonlocal resume_reductions
        if isinstance(command, ResumeGraphNodes):
            resume_reductions += 1
        return original_reduce(state, command)

    monkeypatch.setattr(family_driver, "reduce_graph_run", count_reduce)
    completed = await graph.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(resume_empty_interrupt(graph, awaiting, "leaf"),),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert resume_reductions == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_commit", [False, True], ids=("error", "cancellation"))
async def test_first_setup_transition_failure_releases_without_aborting(
    cancel_commit: bool,
) -> None:
    class SetupCommitError(RuntimeError):
        pass

    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    child = Graph[str]("ownership.setup-transition-failure.child")
    child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    child.add_node("leaf", interrupt, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("ownership.setup-transition-failure.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    awaiting = await parent.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    original: BaseException = (
        asyncio.CancelledError("resume commit cancelled after write")
        if cancel_commit
        else SetupCommitError("resume commit failed")
    )
    transitions: list[GraphTransition[str]] = []

    async def reject_resume(transition: GraphTransition[str], /) -> GraphRunState:
        transitions.append(transition)
        if isinstance(transition.command, ResumeGraphNodes):
            raise original
        return transition.candidate_state

    with pytest.raises(type(original)) as raised:
        await parent.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            resume=(resume_empty_interrupt(parent, awaiting, "leaf", scope=("nested",)),),
            commit=reject_resume,
        )

    assert raised.value is original
    assert [type(transition.command) for transition in transitions] == [ResumeGraphNodes]


@pytest.mark.asyncio
async def test_existing_child_handoff_failure_cleans_a_constructed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, state, _owner, _parent, child_scope, activation, child_state = nested_runtime()
    binding = ChildStateBinding(child_scope, activation, child_state)
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class CandidateError(RuntimeError):
        pass

    original = CandidateError("child handle handoff failed")
    original_accept = _GraphRun[str].accept_child_call

    def reject_child_handoff(
        self: _GraphRun[str],
        position: tuple[int, ...],
        parent_activation: GraphActivationIdentity,
        phase: _ChildPhase[str],
        handle: _ChildHandle[str] | None,
    ) -> None:
        if handle is not None:
            raise original
        original_accept(self, position, parent_activation, phase, handle)

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if transition.scope and isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    monkeypatch.setattr(_GraphRun, "accept_child_call", reject_child_handoff)
    task = asyncio.create_task(
        admit_continuation_root(
            graph,
            state,
            (binding,),
            commit=commit,
        )
    )
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    cleanup_release.set()

    with pytest.raises(CandidateError) as raised:
        await task

    assert raised.value is original


@pytest.mark.asyncio
async def test_transitioned_child_handoff_base_signal_releases_without_stale_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, state, _owner, _parent, child_scope, activation, child_state = nested_runtime()
    root_state = leased_state(state)
    binding = ChildStateBinding(child_scope, activation, child_state)
    _planned, fences = plan_fences(graph, lineage_states(root_state, (binding,)))
    handles: list[_ChildHandle[str]] = []
    transitions: list[GraphTransition[str]] = []

    class HandoffSignal(BaseException):
        pass

    original = HandoffSignal("child handoff interrupted")

    def interrupt_handoff(
        _owner: _GraphRun[str],
        _position: tuple[int, ...],
        _parent: GraphActivationIdentity,
        _phase: _ChildPhase[str],
        handle: _ChildHandle[str] | None,
    ) -> None:
        assert handle is not None
        handles.append(handle)
        raise original

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        transitions.append(transition)
        return transition.candidate_state

    monkeypatch.setattr(_GraphRun, "accept_child_call", interrupt_handoff)
    with pytest.raises(HandoffSignal) as raised:
        await admit_continuation_root(
            graph,
            root_state,
            (binding,),
            commit=commit,
            fences=fences,
        )

    assert raised.value is original
    assert tuple(transition.scope for transition in transitions) == ((),)
    assert transitions[0].command == fences[0].command
    assert len(handles) == 1
    with pytest.raises(ResultCollectionError, match="already released"):
        await handles[0].drive()


@pytest.mark.asyncio
async def test_child_constructor_failure_after_root_fence_preserves_the_confirmed_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, state, _owner, _parent, child_scope, activation, child_state = nested_runtime()
    root_state = leased_state(state)
    binding = ChildStateBinding(child_scope, activation, child_state)
    _planned, fences = plan_fences(graph, lineage_states(root_state, (binding,)))
    transitions: list[GraphTransition[str]] = []

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("child owner construction failed")

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        transitions.append(transition)
        return transition.candidate_state

    fail_owner_construction(monkeypatch, original, scope_depth=1)
    with pytest.raises(Graph.Error) as raised:
        await admit_continuation_root(
            graph,
            root_state,
            (binding,),
            commit=commit,
            fences=fences,
        )

    partial = cast(Graph.PartialCommitError[str], raised.value)
    assert isinstance(partial, Graph.PartialCommitError)
    assert partial.cause is original
    assert partial.failed_scope == ("nested",)
    assert partial.state == transitions[0].candidate_state
    assert tuple(transition.scope for transition in transitions) == ((),)


@pytest.mark.asyncio
async def test_descendant_constructor_failure_aborts_each_constructed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grandchild = Graph[str]("ownership.descendant-validation.grandchild")
    grandchild.add_node("leaf", produce, inputs={}, outputs={"value": str})
    grandchild.set_outputs({})
    child = Graph[str]("ownership.descendant-validation.child")
    child.add_node("grandchild", grandchild, inputs={})
    child.set_outputs({})
    parent_graph = Graph[str]("ownership.descendant-validation.parent")
    parent_graph.add_node("child", child, inputs={})
    parent_graph.set_outputs({})
    graph = parent_graph._compile().graph
    state = running_state(
        definition_id=graph.definition_id,
        version=graph.version,
        frontier=("child",),
        run_id="parent",
    )
    parent = GraphActivationIdentity(state.run_id, state.superstep, GraphNodeId("child"))
    scope_run = root_scope_run(state.run_id)
    child_scope = child_scope_run_for_activation(scope_run, parent)
    child_graph = graph.nested_graphs[parent.node_id]
    child_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, child_scope.graph_run_id, parent),
    )
    child_binding = ChildStateBinding(
        child_scope,
        StableActivation(scope_run, parent.superstep, parent.node_id),
        child_state,
    )
    grandchild_parent = GraphActivationIdentity(
        child_state.run_id,
        child_state.superstep,
        GraphNodeId("grandchild"),
    )
    grandchild_scope = child_scope_run_for_activation(child_scope, grandchild_parent)
    grandchild_state = reduce_graph_run(
        None,
        project_start_graph_command(
            child_graph.nested_graphs[grandchild_parent.node_id],
            grandchild_scope.graph_run_id,
            grandchild_parent,
        ),
    )
    grandchild_binding = ChildStateBinding(
        grandchild_scope,
        StableActivation(child_scope, grandchild_parent.superstep, grandchild_parent.node_id),
        grandchild_state,
    )
    transitions: list[GraphTransition[str]] = []

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        transitions.append(transition)
        return transition.candidate_state

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("grandchild owner construction failed")
    fail_owner_construction(monkeypatch, original, scope_depth=2)
    with pytest.raises(ConstructionError) as raised:
        await admit_continuation_root(
            graph,
            state,
            (child_binding, grandchild_binding),
            commit=commit,
        )

    assert raised.value is original
    assert tuple(transition.scope for transition in transitions if isinstance(transition.command, AbortGraphRun)) == (
        ("child", "grandchild"),
        ("child",),
        (),
    )


@pytest.mark.asyncio
async def test_drive_rejects_an_active_projection_without_a_child_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _graph, _state, owner, parent, _child_scope, _activation, _child_state = nested_runtime()

    def forged_prepare(
        _executor: GraphExecutor[str],
        _request: StepRequest[str],
    ) -> WaitingForChildren[str]:
        return WaitingForChildren((), (ActiveChild(parent),))

    monkeypatch.setattr(GraphExecutor, "prepare", forged_prepare)

    with pytest.raises(ResultCollectionError, match="no admitted child call"):
        await owner.drive_quantum()


@pytest.mark.asyncio
async def test_terminal_root_construction_failure_needs_no_synthetic_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, state, _owner, _parent, _child_scope, _activation, _child_state = nested_runtime()
    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("terminal root owner construction failed")
    fail_owner_construction(monkeypatch, original, scope_depth=0)
    transitions: list[GraphTransition[str]] = []

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        transitions.append(transition)
        return transition.candidate_state

    with pytest.raises(ConstructionError) as raised:
        await admit_continuation_root(graph, completed, (), commit=commit)

    assert raised.value is original
    assert transitions == []


@pytest.mark.asyncio
async def test_abort_preserves_first_child_session_fence_or_state_error() -> None:
    graph, state, _owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    class CleanupError(RuntimeError):
        pass

    child_error = CleanupError("child abort failed")

    async def no_drive() -> tuple[AwaitingResume, None, None]:
        return AwaitingResume(()), None, None

    async def fail_abort(_reason: GraphAbortReason) -> None:
        raise child_error

    async def no_release() -> None:
        return None

    handle = _ChildHandle[str](no_drive, fail_abort, no_release)
    child_owner = root_owner(graph, completed)
    child_owner._children.append(((0, 0), parent, ActiveChild(parent), handle))
    with pytest.raises(CleanupError) as raised_child:
        await child_owner.abort(GraphAbortReason("abort"))
    assert raised_child.value is child_error

    skipped_owner = root_owner(graph, completed)
    skipped_owner._children.append(((0, 0), parent, AbortedChild(parent, GraphAbortReason("done")), handle))
    await skipped_owner.abort(GraphAbortReason("ignored"))

    session_error = CleanupError("session close failed")

    class FailingSession:
        async def aclose(self) -> None:
            raise session_error

    session_owner = root_owner(graph, completed)
    session_owner._session = cast(GraphExecutionSession[str], FailingSession())
    with pytest.raises(CleanupError) as raised_session:
        await session_owner.abort(GraphAbortReason("abort"))
    assert raised_session.value is session_error

    fence_error = CleanupError("fence failed")

    async def reject_fence(_transition: GraphTransition[str], /) -> GraphRunState:
        raise fence_error

    fenced_owner = root_owner(graph, leased_state(state), commit=reject_fence)
    with pytest.raises(CleanupError) as raised_fence:
        await fenced_owner.abort(GraphAbortReason("abort"))
    assert raised_fence.value is fence_error

    state_error = CleanupError("abort failed")

    async def reject_abort(_transition: GraphTransition[str], /) -> GraphRunState:
        raise state_error

    state_owner = root_owner(graph, state, commit=reject_abort)
    with pytest.raises(CleanupError) as raised_state:
        await state_owner.abort(GraphAbortReason("abort"))
    assert raised_state.value is state_error


@pytest.mark.asyncio
async def test_release_preserves_child_or_session_errors_and_allows_retry() -> None:
    graph, state, _owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    class CleanupError(RuntimeError):
        pass

    async def no_drive() -> tuple[AwaitingResume, None, None]:
        return AwaitingResume(()), None, None

    async def no_abort(_reason: GraphAbortReason) -> None:
        return None

    release_error = CleanupError("child release failed")

    async def fail_release() -> None:
        raise release_error

    release_owner = root_owner(graph, completed)
    release_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](no_drive, no_abort, fail_release),
        )
    )
    with pytest.raises(CleanupError) as raised_release:
        await release_owner.release()
    assert raised_release.value is release_error

    session_error = CleanupError("session release failed")

    class FailingSession:
        async def aclose(self) -> None:
            raise session_error

    session_owner = root_owner(graph, completed)
    session_owner._session = cast(GraphExecutionSession[str], FailingSession())
    with pytest.raises(CleanupError) as raised_session:
        await session_owner.release()
    assert raised_session.value is session_error

    attempts = 0

    async def release_on_retry() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise release_error

    retry_owner = root_owner(graph, completed)
    retry_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            _ChildHandle[str](no_drive, no_abort, release_on_retry),
        )
    )
    with pytest.raises(CleanupError):
        await retry_owner.release()
    await retry_owner.release()
    assert attempts == 2


@pytest.mark.asyncio
async def test_facade_cleanup_preserves_cancellation_over_abort_and_release_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def block(_values: Graph.Values[str]) -> Graph.Values[str]:
        started.set()
        await asyncio.Event().wait()
        return Graph.values()

    graph = Graph[str]("ownership.facade-abort-error")
    graph.add_node("node", block, inputs={}, outputs={})
    graph.set_outputs({})

    class CleanupError(RuntimeError):
        pass

    abort_error = CleanupError("abort failed")
    release_error = CleanupError("release failed")

    async def fail_abort(_owner: _GraphRun[str], _reason: GraphAbortReason) -> None:
        raise abort_error

    async def fail_release(_owner: _GraphRun[str]) -> None:
        raise release_error

    monkeypatch.setattr(_GraphRun, "abort", fail_abort)
    monkeypatch.setattr(_GraphRun, "release", fail_release)
    task = asyncio.create_task(graph.run(Graph.values()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as raised:
        await task

    assert raised.value.__cause__ is abort_error


@pytest.mark.asyncio
async def test_facade_success_surfaces_release_error(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = Graph[str]("ownership.facade-release-error")
    graph.add_node("node", produce, inputs={}, outputs={"value": str})
    graph.set_outputs({})

    class ReleaseError(RuntimeError):
        pass

    original = ReleaseError("release failed")

    async def fail_release(_owner: _GraphRun[str]) -> None:
        raise original

    monkeypatch.setattr(_GraphRun, "release", fail_release)

    with pytest.raises(ReleaseError) as raised:
        await graph.run(Graph.values())

    assert raised.value is original


@pytest.mark.asyncio
@pytest.mark.parametrize("block_start", [True, False], ids=("start", "owner-transition"))
async def test_commit_acknowledgement_is_linearized_before_cancellation_cleanup(
    block_start: bool,
) -> None:
    graph = Graph[str](f"ownership.commit-cancellation.{block_start}")
    graph.add_node("node", produce, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    authoritative: GraphRunState | None = None
    write_finished = asyncio.Event()
    acknowledge = asyncio.Event()
    blocked = False

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        nonlocal authoritative, blocked
        if transition.previous_state != authoritative:
            raise RuntimeError("stale transition reached the authoritative commit port")
        authoritative = transition.candidate_state
        should_block = not blocked and (
            (block_start and transition.previous_state is None)
            or (
                not block_start
                and transition.previous_state is not None
                and not isinstance(transition.command, AbortGraphRun)
            )
        )
        if should_block:
            blocked = True
            write_finished.set()
            await acknowledge.wait()
        return transition.candidate_state

    task = asyncio.create_task(graph.run(Graph.values(), commit=commit))
    await write_finished.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    acknowledge.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert authoritative is not None
    assert authoritative.status is GraphRunStatus.ABORTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_at",
    ["root-claim", "child-start", "child-claim"],
)
async def test_commit_origin_cancellation_never_aborts_from_an_unconfirmed_snapshot(
    cancel_at: str,
) -> None:
    if cancel_at == "root-claim":
        graph = Graph[str]("ownership.commit-origin.root")
        graph.add_node("node", produce, inputs={}, outputs={"value": str})
        graph.set_outputs({})
    else:
        child = Graph[str](f"ownership.commit-origin.{cancel_at}.child")
        child.add_node("leaf", produce, inputs={}, outputs={"value": str})
        child.set_outputs({})
        graph = Graph[str](f"ownership.commit-origin.{cancel_at}.parent")
        graph.add_node("nested", child, inputs={})
        graph.set_outputs({})
    authoritative: dict[tuple[str, ...], GraphRunState] = {}
    transitions: list[GraphTransition[str]] = []
    cancelled = False
    cancelled_scope: tuple[str, ...] | None = None

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        nonlocal cancelled, cancelled_scope
        if transition.previous_state != authoritative.get(transition.scope):
            raise RuntimeError("stale cleanup transition reached the authoritative commit port")
        authoritative[transition.scope] = transition.candidate_state
        transitions.append(transition)
        is_claim = transition.previous_state is not None and transition.candidate_state.execution is not None
        should_cancel = (
            (cancel_at == "root-claim" and not transition.scope and is_claim)
            or (cancel_at == "child-start" and bool(transition.scope) and transition.previous_state is None)
            or (cancel_at == "child-claim" and bool(transition.scope) and is_claim)
        )
        if should_cancel and not cancelled:
            cancelled = True
            cancelled_scope = transition.scope
            raise asyncio.CancelledError("commit cancelled after write")
        return transition.candidate_state

    with pytest.raises(asyncio.CancelledError, match="commit cancelled after write") as raised:
        await graph.run(Graph.values(), commit=commit)

    assert raised.value.__cause__ is None
    assert cancelled
    assert cancelled_scope is not None
    assert authoritative[cancelled_scope].status is GraphRunStatus.RUNNING
    assert not any(isinstance(transition.command, AbortGraphRun) for transition in transitions)


@pytest.mark.asyncio
async def test_facade_cleanup_finishes_after_repeated_waiter_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def block(_values: Graph.Values[str]) -> Graph.Values[str]:
        node_started.set()
        await asyncio.Event().wait()
        return Graph.values()

    async def delayed_abort(_owner: _GraphRun[str], _reason: GraphAbortReason) -> None:
        cleanup_started.set()
        await cleanup_release.wait()

    graph = Graph[str]("ownership.facade-repeated-cancellation")
    graph.add_node("node", block, inputs={}, outputs={})
    graph.set_outputs({})
    monkeypatch.setattr(_GraphRun, "abort", delayed_abort)
    task = asyncio.create_task(graph.run(Graph.values()))
    await node_started.wait()
    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    cleanup_release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_fresh_root_setup_cleanup_survives_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("root construction failed")

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    graph = Graph[str]("ownership.fresh-root-cleanup-cancellation")
    graph.add_node("node", produce, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    fail_owner_construction(monkeypatch, original, scope_depth=0)
    task = asyncio.create_task(graph.run(Graph.values(), commit=commit))
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    cleanup_release.set()

    with pytest.raises(ConstructionError) as raised:
        await task

    assert raised.value is original


@pytest.mark.asyncio
async def test_fresh_child_setup_cleanup_survives_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("child construction failed")

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if transition.scope and isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    child = Graph[str]("ownership.fresh-child-cleanup-cancellation.child")
    child.add_node("leaf", produce, inputs={}, outputs={"value": str})
    child.set_outputs({})
    parent = Graph[str]("ownership.fresh-child-cleanup-cancellation.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    fail_owner_construction(monkeypatch, original, scope_depth=1)
    task = asyncio.create_task(parent.run(Graph.values(), commit=commit))
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    cleanup_release.set()

    with pytest.raises(ConstructionError) as raised:
        await task

    assert raised.value is original


@pytest.mark.asyncio
async def test_continued_root_setup_cleanup_survives_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    graph = Graph[str]("ownership.continued-root-cleanup-cancellation")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("node", interrupt, inputs={}, outputs={})
    graph.set_outputs({})
    awaiting = await graph.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("continued root construction failed")

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    fail_owner_construction(monkeypatch, original, scope_depth=0)
    task = asyncio.create_task(
        graph.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            commit=commit,
        )
    )
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    cleanup_release.set()

    with pytest.raises(ConstructionError) as raised:
        await task

    assert raised.value is original


@pytest.mark.asyncio
async def test_continued_child_setup_cleanup_survives_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    child = Graph[str]("ownership.continued-child-cleanup-cancellation.child")
    child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    child.add_node("leaf", interrupt, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("ownership.continued-child-cleanup-cancellation.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    awaiting = await parent.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("continued child construction failed")

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if transition.scope and isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    fail_owner_construction(monkeypatch, original, scope_depth=1)
    task = asyncio.create_task(
        parent.run(
            state=awaiting.state,
            continuation=awaiting.continuation,
            commit=commit,
        )
    )
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    cleanup_release.set()

    with pytest.raises(ConstructionError) as raised:
        await task

    assert raised.value is original

# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.engine.factories import compiled_graph, leased_state, running_state

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.family_driver import (
    GraphCommit,
    GraphTransition,
    _binding_at,
    _ChildHandle,
    _EvidenceReader,
    _executor_at,
    _frames_for_owners,
    _GraphRun,
    _opaque_handle,
    _subtree_bindings,
    admit_root,
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
from mote_kernel.execution.invocation import lineage_states, plan_resumes
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import ResumeFailedNodeRequest, StepRequest, UseMaterializedInput
from mote_kernel.execution.result import (
    AbortedChild,
    AbortedGraph,
    ActiveChild,
    AwaitingResume,
    CompletedChild,
    CompletedGraph,
    MissingChild,
    TaskFailure,
    WaitingForChildren,
)
from mote_kernel.execution.run_context import (
    ChildBoundaryAvailabilityCoordinate,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    GraphAbortReason,
    GraphFrontierState,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    ParentGraphActivation,
    reduce_graph_run,
)


async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(value="output")


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
    return _GraphRun(
        graph,
        scope_run,
        state,
        ScopedFrameIndex() if frames is None else frames,
        GraphExecutor(graph),
        ExecutionLimits(),
        commit,
        (),
        None,
    )


def nested_runtime() -> tuple[
    CompiledGraph[str],
    GraphRunState,
    _GraphRun[str],
    ParentGraphActivation,
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
    parent = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("nested"))
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
    declarations = tuple(
        (declaration.name, declaration.descriptor) for declaration in graph.graph_output_descriptor.declarations.entries
    )
    return _make_graph_output_view((NamedValue("value", value),), declarations)


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
        GraphExecutor(graph).start_command(scope_run.graph_run_id),
        None,
        capture,
    )
    foreign = ScopeRunCoordinate((GraphNodeId("foreign"),), scope_run.graph_run_id)

    with pytest.raises(SnapshotMismatchError, match="different scoped graph run"):
        await scoped_commit(foreign, None)(transitions[0])


def test_binding_partition_requires_known_children_and_finds_descendants() -> None:
    root = root_scope_run(GraphRunId("root"))
    child = ScopeRunCoordinate((GraphNodeId("child"),), GraphRunId("child"))
    grandchild = ScopeRunCoordinate(
        (GraphNodeId("child"), GraphNodeId("grandchild")),
        GraphRunId("grandchild"),
    )
    child_activation = StableActivation(root, 0, GraphNodeId("child"))
    grandchild_activation = StableActivation(child, 0, GraphNodeId("grandchild"))
    child_binding = ChildStateBinding(child, child_activation, running_state(run_id=child.graph_run_id))
    grandchild_binding = ChildStateBinding(
        grandchild,
        grandchild_activation,
        running_state(run_id=grandchild.graph_run_id),
    )

    assert _subtree_bindings(child, (child_binding, grandchild_binding)) == (
        child_binding,
        grandchild_binding,
    )
    assert _binding_at((child_binding,), child) is child_binding

    unknown = ScopeRunCoordinate((GraphNodeId("unknown"),), GraphRunId("unknown"))
    boundary = ConfirmedChildBoundary(
        ChildBoundaryAvailabilityCoordinate(unknown, nested_graph().graph_output_descriptor.identity),
        _make_graph_output_view((), ()),
    )
    with pytest.raises(SnapshotMismatchError, match="no child binding"):
        _frames_for_owners(ScopedFrameIndex(child_boundaries=(boundary,)), (), frozenset({root}))


def test_graph_owner_rejects_foreign_scope_and_duplicate_or_unknown_positions() -> None:
    graph, state, owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    with pytest.raises(SnapshotMismatchError, match="scoped definition"):
        _GraphRun(
            graph,
            ScopeRunCoordinate((GraphNodeId("foreign"),), state.run_id),
            state,
            ScopedFrameIndex(),
            GraphExecutor(graph),
            ExecutionLimits(),
            None,
            (),
            None,
        )

    position = owner._new_position(parent)
    owner._children.append((position, parent, ActiveChild(parent), None, None))
    with pytest.raises(ResultCollectionError, match="more than one child call"):
        owner._new_position(parent)

    unknown = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("unknown"))
    with pytest.raises(ResultCollectionError, match="not part of the parent definition"):
        owner._new_position(unknown)


def test_terminal_boundary_installation_is_exact_and_idempotent() -> None:
    graph, state, _owner, parent, child_scope, _activation, _child_state = nested_runtime()
    child_graph = graph.nested_graphs[parent.node_id]
    output = graph_output(child_graph, "output")
    other = graph_output(child_graph, "other")
    coordinate: ChildBoundaryAvailabilityCoordinate[str] = ChildBoundaryAvailabilityCoordinate(
        child_scope,
        child_graph.graph_output_descriptor.identity,
    )
    boundary = ConfirmedChildBoundary(coordinate, output)

    aborted_owner = root_owner(graph, state)
    aborted_owner._children.append(((0, 0), parent, AbortedChild(parent, GraphAbortReason("aborted")), None, None))
    with pytest.raises(ResultCollectionError, match="aborted child"):
        aborted_owner._install_terminal(0, boundary)

    missing_owner = root_owner(graph, state)
    missing_owner._children.append(((0, 0), parent, CompletedChild(parent, output), None, None))
    with pytest.raises(ResultCollectionError, match="exact output boundary"):
        missing_owner._install_terminal(0, None)

    foreign_owner = root_owner(graph, state)
    foreign_owner._children.append(((0, 0), parent, CompletedChild(parent, output), None, None))
    foreign_coordinate: ChildBoundaryAvailabilityCoordinate[str] = ChildBoundaryAvailabilityCoordinate(
        ScopeRunCoordinate((GraphNodeId("foreign"),), child_scope.graph_run_id),
        child_graph.graph_output_descriptor.identity,
    )
    with pytest.raises(SnapshotMismatchError, match="parent definition"):
        foreign_owner._install_terminal(0, ConfirmedChildBoundary(foreign_coordinate, output))

    mismatch_owner = root_owner(
        graph,
        state,
        frames=ScopedFrameIndex(child_boundaries=(ConfirmedChildBoundary(coordinate, other),)),
    )
    mismatch_owner._children.append(((0, 0), parent, CompletedChild(parent, output), None, None))
    with pytest.raises(SnapshotMismatchError, match="confirmed boundary"):
        mismatch_owner._install_terminal(0, boundary)

    existing_owner = root_owner(
        graph,
        state,
        frames=ScopedFrameIndex(child_boundaries=(boundary,)),
    )
    existing_owner._children.append(((0, 0), parent, CompletedChild(parent, output), None, None))
    existing_owner._install_terminal(0, boundary)


@pytest.mark.asyncio
async def test_child_start_rejects_a_stale_parent_activation() -> None:
    _graph, state, owner, _parent, _child_scope, _activation, _child_state = nested_runtime()
    stale = ParentGraphActivation(state.run_id, state.superstep + 1, GraphNodeId("nested"))

    with pytest.raises(ResultCollectionError, match="stale or foreign"):
        await owner._start_child(MissingChild(stale))


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

    def evidence() -> tuple[tuple[ChildStateBinding, ...], ScopedFrameIndex[str]]:
        return (), ScopedFrameIndex()

    invalid_owner = root_owner(graph, state)
    invalid_owner._children.append(((0, 0), parent, AwaitingResume((), ()), None, None))
    await invalid_owner._drive_child(0)

    async def completed() -> CompletedGraph:
        return CompletedGraph()

    def aborted_terminal() -> tuple[AbortedChild, _EvidenceReader[str], None]:
        return AbortedChild(parent, GraphAbortReason("aborted")), evidence, None

    completed_owner = root_owner(graph, state)
    completed_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            cast(_ChildHandle[str], (completed, no_abort, no_release, aborted_terminal, evidence)),
            None,
        )
    )
    with pytest.raises(ResultCollectionError, match="non-completed"):
        await completed_owner._drive_child(0)

    async def aborted() -> AbortedGraph:
        return AbortedGraph()

    def completed_terminal() -> tuple[
        CompletedChild[str],
        _EvidenceReader[str],
        ConfirmedChildBoundary[str],
    ]:
        return CompletedChild(parent, output), evidence, boundary

    aborted_owner = root_owner(graph, state)
    aborted_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            cast(_ChildHandle[str], (aborted, no_abort, no_release, completed_terminal, evidence)),
            None,
        )
    )
    with pytest.raises(ResultCollectionError, match="non-aborted"):
        await aborted_owner._drive_child(0)


@pytest.mark.asyncio
async def test_nested_settlement_requires_one_terminal_child_call() -> None:
    _graph, state, owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    task = GraphTask(TaskId("nested"), state.run_id, state.superstep, parent.node_id)
    result = TaskFailure(task, "failed")

    with pytest.raises(ResultCollectionError, match="no admitted child call"):
        await owner._retire_child(result)

    owner._children.append(((0, 0), parent, ActiveChild(parent), None, None))
    with pytest.raises(ResultCollectionError, match="unretired terminal child"):
        await owner._retire_child(result)


def test_owner_evidence_cannot_cross_root_child_roles_or_lose_a_reader() -> None:
    graph, _state, owner, parent, child_scope, activation, child_state = nested_runtime()
    with pytest.raises(SnapshotMismatchError, match="root graph evidence"):
        owner.freeze_child_evidence()

    child_graph = graph.nested_graphs[parent.node_id]
    child_owner = _GraphRun(
        child_graph,
        child_scope,
        child_state,
        ScopedFrameIndex(),
        GraphExecutor(child_graph),
        ExecutionLimits(),
        None,
        (0, 0),
        activation,
    )
    with pytest.raises(SnapshotMismatchError, match="child graph evidence"):
        child_owner.freeze_root_evidence()

    owner._children.append(((0, 0), parent, ActiveChild(parent), None, None))
    with pytest.raises(SnapshotMismatchError, match="no export evidence"):
        owner.freeze_root_evidence()


@pytest.mark.asyncio
async def test_opaque_handle_is_one_shot_and_inert_after_release() -> None:
    graph, _state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    aborted_state = reduce_graph_run(
        child_state,
        AbortGraphRun(child_state.revision, GraphAbortReason("aborted")),
    )
    child_graph = graph.nested_graphs[parent.node_id]
    child_owner = _GraphRun(
        child_graph,
        child_scope,
        aborted_state,
        ScopedFrameIndex(),
        GraphExecutor(child_graph),
        ExecutionLimits(),
        None,
        (0, 0),
        activation,
    )
    handle = _opaque_handle(child_owner, parent)

    terminal, _reader, boundary = handle[3]()
    assert isinstance(terminal, AbortedChild)
    assert boundary is None
    with pytest.raises(ResultCollectionError, match="only be consumed once"):
        handle[3]()

    await handle[2]()
    await handle[2]()
    await handle[1](GraphAbortReason("ignored"))
    with pytest.raises(ResultCollectionError, match="already released"):
        await handle[0]()

    repeated = root_owner(graph, running_state(definition_id=graph.definition_id, frontier=("nested",)))
    await repeated.release()
    await repeated.release()


def test_executor_lookup_requires_an_exact_owner_coordinate() -> None:
    with pytest.raises(SnapshotMismatchError, match="no executor"):
        _executor_at((), root_scope_run(GraphRunId("missing")))


def test_resume_planning_requires_an_executor_for_the_resumed_owner() -> None:
    graph = compiled_graph("a")
    state = running_state()
    action: ResumeFailedNodeRequest[str] = ResumeFailedNodeRequest(
        (),
        GraphNodeId("a"),
        UseMaterializedInput(),
    )

    with pytest.raises(SnapshotMismatchError, match="no live executor"):
        plan_resumes(
            graph,
            lineage_states(state, ()),
            ScopedFrameIndex(),
            (action,),
            (),
        )


@pytest.mark.asyncio
async def test_existing_child_admission_rejects_unknown_or_malformed_terminal_bindings() -> None:
    graph, state, owner, _parent, child_scope, activation, child_state = nested_runtime()
    unknown_parent = StableActivation(root_scope_run(state.run_id), state.superstep, GraphNodeId("unknown"))
    unknown_binding = ChildStateBinding(
        ScopeRunCoordinate((GraphNodeId("unknown"),), GraphRunId("unknown")),
        unknown_parent,
        child_state,
    )
    with pytest.raises(SnapshotMismatchError, match="no parent nested definition"):
        await owner.admit_existing_children((unknown_binding,), ScopedFrameIndex(), ())

    malformed = replace(child_state, status=GraphRunStatus.ABORTED, abort=None)
    binding = ChildStateBinding(child_scope, activation, malformed)
    with pytest.raises(SnapshotMismatchError, match="no canonical outcome"):
        await root_owner(graph, state).admit_existing_children((binding,), ScopedFrameIndex(), ())


@pytest.mark.asyncio
async def test_existing_child_setup_cleans_a_constructed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, state, owner, parent, child_scope, activation, child_state = nested_runtime()
    binding = ChildStateBinding(child_scope, activation, child_state)
    original_admit = _GraphRun[str].admit_existing_children

    class CandidateError(RuntimeError):
        pass

    original = CandidateError("child recursive admission failed")

    async def reject_child(
        candidate: _GraphRun[str],
        bindings: tuple[ChildStateBinding, ...],
        frames: ScopedFrameIndex[str],
        executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[str]], ...],
    ) -> None:
        if candidate._parent_activation is not None:
            raise original
        await original_admit(candidate, bindings, frames, executors)

    monkeypatch.setattr(_GraphRun, "admit_existing_children", reject_child)

    with pytest.raises(CandidateError) as raised:
        await owner.admit_existing_children(
            (binding,),
            ScopedFrameIndex(),
            ((child_scope, GraphExecutor(graph.nested_graphs[parent.node_id])),),
        )

    assert raised.value is original
    assert owner.state == state


@pytest.mark.asyncio
async def test_unconstructed_child_cleanup_skips_terminal_descendants() -> None:
    _graph, state, owner, _parent, child_scope, activation, child_state = nested_runtime()
    child_binding = ChildStateBinding(child_scope, activation, child_state)
    grandchild_scope = ScopeRunCoordinate(
        (*child_scope.scope, GraphNodeId("grandchild")),
        GraphRunId("grandchild"),
    )
    grandchild_activation = StableActivation(child_scope, 0, GraphNodeId("grandchild"))
    terminal_grandchild = replace(
        child_state,
        run_id=grandchild_scope.graph_run_id,
        status=GraphRunStatus.COMPLETED,
        frontier=GraphFrontierState(()),
    )
    grandchild_binding = ChildStateBinding(
        grandchild_scope,
        grandchild_activation,
        terminal_grandchild,
    )

    with pytest.raises(SnapshotMismatchError, match="no executor"):
        await owner.admit_existing_children(
            (child_binding, grandchild_binding),
            ScopedFrameIndex(),
            (),
        )

    assert owner.state == state


@pytest.mark.asyncio
async def test_drive_rejects_an_active_projection_without_a_child_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _graph, _state, owner, parent, _child_scope, _activation, _child_state = nested_runtime()

    async def forged_prepare(
        _executor: GraphExecutor[str],
        _request: StepRequest[str],
    ) -> WaitingForChildren[str]:
        return WaitingForChildren((), (ActiveChild(parent),))

    monkeypatch.setattr(GraphExecutor, "prepare", forged_prepare)

    with pytest.raises(ResultCollectionError, match="no admitted child call"):
        await owner.drive_quantum()


@pytest.mark.asyncio
async def test_terminal_root_constructor_failure_needs_no_synthetic_abort() -> None:
    graph, state, _owner, _parent, child_scope, activation, child_state = nested_runtime()
    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    with pytest.raises(SnapshotMismatchError, match="no executor"):
        await admit_root(
            graph,
            completed,
            (),
            ScopedFrameIndex(),
            (),
            ExecutionLimits(),
            None,
        )

    terminal_child = replace(
        child_state,
        status=GraphRunStatus.COMPLETED,
        frontier=GraphFrontierState(()),
    )
    binding = ChildStateBinding(child_scope, activation, terminal_child)
    with pytest.raises(SnapshotMismatchError, match="no executor"):
        await admit_root(
            graph,
            state,
            (binding,),
            ScopedFrameIndex(),
            (),
            ExecutionLimits(),
            None,
        )


@pytest.mark.asyncio
async def test_abort_preserves_first_child_session_fence_or_state_error() -> None:
    graph, state, _owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    class CleanupError(RuntimeError):
        pass

    child_error = CleanupError("child abort failed")

    async def no_drive() -> AwaitingResume:
        return AwaitingResume((), ())

    async def fail_abort(_reason: GraphAbortReason) -> None:
        raise child_error

    async def no_release() -> None:
        return None

    def evidence() -> tuple[tuple[ChildStateBinding, ...], ScopedFrameIndex[str]]:
        return (), ScopedFrameIndex()

    def no_terminal() -> tuple[AbortedChild, _EvidenceReader[str], None]:
        raise AssertionError("not consumed")

    handle = cast(_ChildHandle[str], (no_drive, fail_abort, no_release, no_terminal, evidence))
    child_owner = root_owner(graph, completed)
    child_owner._children.append(((0, 0), parent, ActiveChild(parent), handle, None))
    with pytest.raises(CleanupError) as raised_child:
        await child_owner.abort(GraphAbortReason("abort"))
    assert raised_child.value is child_error

    skipped_owner = root_owner(graph, completed)
    skipped_owner._children.append(((0, 0), parent, AbortedChild(parent, GraphAbortReason("done")), handle, evidence))
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
async def test_release_preserves_export_child_or_session_errors_and_allows_retry() -> None:
    graph, state, _owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    class CleanupError(RuntimeError):
        pass

    async def no_drive() -> AwaitingResume:
        return AwaitingResume((), ())

    async def no_abort(_reason: GraphAbortReason) -> None:
        return None

    async def no_release() -> None:
        return None

    def no_terminal() -> tuple[AbortedChild, _EvidenceReader[str], None]:
        raise AssertionError("not consumed")

    export_error = CleanupError("export failed")

    def fail_export() -> tuple[tuple[ChildStateBinding, ...], ScopedFrameIndex[str]]:
        raise export_error

    export_owner = root_owner(graph, completed)
    export_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            cast(_ChildHandle[str], (no_drive, no_abort, no_release, no_terminal, fail_export)),
            None,
        )
    )
    with pytest.raises(CleanupError) as raised_export:
        await export_owner.release()
    assert raised_export.value is export_error

    release_error = CleanupError("child release failed")

    async def fail_release() -> None:
        raise release_error

    def evidence() -> tuple[tuple[ChildStateBinding, ...], ScopedFrameIndex[str]]:
        return (), ScopedFrameIndex()

    release_owner = root_owner(graph, completed)
    release_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            cast(_ChildHandle[str], (no_drive, no_abort, fail_release, no_terminal, evidence)),
            None,
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
            cast(_ChildHandle[str], (no_drive, no_abort, release_on_retry, no_terminal, evidence)),
            None,
        )
    )
    with pytest.raises(CleanupError):
        await retry_owner.release()
    await retry_owner.release()
    assert attempts == 2


@pytest.mark.asyncio
async def test_facade_cleanup_preserves_abort_error_over_release_error(
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

    with pytest.raises(CleanupError) as raised:
        await task

    assert raised.value is abort_error


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

    def reject_state(_executor: GraphExecutor[str], _state: GraphRunState) -> None:
        raise original

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    graph = Graph[str]("ownership.fresh-root-cleanup-cancellation")
    graph.add_node("node", produce, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    monkeypatch.setattr(GraphExecutor, "validate_state", reject_state)
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
    original_validate = GraphExecutor[str].validate_state

    def reject_child(executor: GraphExecutor[str], state: GraphRunState) -> None:
        if executor.graph.definition_scope:
            raise original
        original_validate(executor, state)

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
    monkeypatch.setattr(GraphExecutor, "validate_state", reject_child)
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
    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("retry")

    graph = Graph[str]("ownership.continued-root-cleanup-cancellation")
    graph.add_node("node", fail, inputs={}, outputs={})
    graph.set_outputs({})
    awaiting = await graph.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class ConstructionError(RuntimeError):
        pass

    original = ConstructionError("continued root construction failed")

    def reject_state(_executor: GraphExecutor[str], _state: GraphRunState) -> None:
        raise original

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    monkeypatch.setattr(GraphExecutor, "validate_state", reject_state)
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
    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("retry")

    child = Graph[str]("ownership.continued-child-cleanup-cancellation.child")
    child.add_node("leaf", fail, inputs={}, outputs={})
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
    original_validate = GraphExecutor[str].validate_state

    def reject_child(executor: GraphExecutor[str], state: GraphRunState) -> None:
        if executor.graph.definition_scope:
            raise original
        original_validate(executor, state)

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if transition.scope and isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    monkeypatch.setattr(GraphExecutor, "validate_state", reject_child)
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

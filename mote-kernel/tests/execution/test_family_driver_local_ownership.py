# pyright: reportPrivateUsage=false

import asyncio
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.engine.factories import compiled_graph, leased_state, running_state

import mote_kernel.execution.family_driver as family_driver
from mote_kernel.execution import Graph
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.family_driver import (
    GraphCommit,
    GraphTransition,
    _ChildHandle,
    _evidence_adapter,
    _EvidencePublisher,
    _EvidenceReader,
    _executor_at,
    _frames_for_owners,
    _GraphRun,
    _opaque_handle,
    _subtree_bindings,
    _validate_owner_transition_plans,
    admit_continued_root,
    commit_transition,
    scoped_commit,
    wait_for_owner_task,
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
from mote_kernel.execution.invocation import PlannedResume, lineage_states, plan_fences, plan_resumes
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
    _new_family_identity,
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
    ResumeGraphNodes,
    reduce_graph_run,
)


async def produce(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(value="output")


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
        new_evidence_publisher(),
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


async def admit_continuation_root(
    graph: CompiledGraph[str],
    state: GraphRunState,
    bindings: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[str] | None = None,
    child_executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[str]], ...] = (),
    commit: GraphCommit[str] | None = None,
) -> tuple[_GraphRun[str], _EvidenceReader[str]]:
    scope_run = root_scope_run(state.run_id)
    return await admit_continued_root(
        graph,
        state,
        bindings,
        ScopedFrameIndex() if frames is None else frames,
        ((scope_run, GraphExecutor(graph)), *child_executors),
        ExecutionLimits(),
        commit,
        (),
        (),
        _new_family_identity(),
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
        GraphExecutor(graph).start_command(scope_run.graph_run_id),
        None,
        capture,
    )
    foreign = ScopeRunCoordinate((GraphNodeId("foreign"),), scope_run.graph_run_id)

    with pytest.raises(SnapshotMismatchError, match="different scoped graph run"):
        await scoped_commit(foreign, None)(transitions[0])


@pytest.mark.asyncio
async def test_owner_task_wait_preserves_an_inner_cancellation() -> None:
    async def cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await wait_for_owner_task(asyncio.create_task(cancel()))


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
    unknown = ScopeRunCoordinate((GraphNodeId("unknown"),), GraphRunId("unknown"))
    boundary = ConfirmedChildBoundary(
        ChildBoundaryAvailabilityCoordinate(unknown, nested_graph().graph_output_descriptor.identity),
        _make_graph_output_view((), ()),
    )
    with pytest.raises(SnapshotMismatchError, match="no child binding"):
        _frames_for_owners(ScopedFrameIndex(child_boundaries=(boundary,)), (), frozenset({root}))

    publish, read = _evidence_adapter((child_binding,), ScopedFrameIndex())
    assert read()[0] == (child_binding,)
    changed_parent = replace(
        child_binding,
        parent_activation=StableActivation(root, 1, GraphNodeId("child")),
    )
    with pytest.raises(SnapshotMismatchError, match="changed its parent activation"):
        publish(changed_parent, ScopedFrameIndex())


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
            new_evidence_publisher(),
        )

    position = owner._new_position(parent)
    with pytest.raises(ResultCollectionError, match="position does not match"):
        owner.accept_child_call((*position, 99), parent, ActiveChild(parent), None)
    owner._children.append((position, parent, ActiveChild(parent), None))
    with pytest.raises(ResultCollectionError, match="more than one child call"):
        owner._new_position(parent)

    unknown = ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("unknown"))
    with pytest.raises(ResultCollectionError, match="not part of the parent definition"):
        owner._new_position(unknown)

    foreign_parent = replace(parent, run_id=GraphRunId("foreign-parent"))
    with pytest.raises(SnapshotMismatchError, match="construction does not match"):
        family_driver._admit_parent_activation(
            root_scope_run(state.run_id),
            foreign_parent,
            graph.nested_graphs[parent.node_id],
        )


def test_child_admits_its_terminal_boundary_before_parent_installation() -> None:
    graph, state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    child_graph = graph.nested_graphs[parent.node_id]
    output = graph_output(child_graph, "output")
    terminal = CompletedChild(parent, output)
    child_owner = _GraphRun(
        child_graph,
        child_scope,
        replace(child_state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(())),
        ScopedFrameIndex(),
        GraphExecutor(child_graph),
        ExecutionLimits(),
        None,
        (0, 0),
        activation,
        new_evidence_publisher(),
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
    stale = ParentGraphActivation(state.run_id, state.superstep + 1, GraphNodeId("nested"))

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
    assert handle is not None and len(handle) == 3
    await owner.abort(GraphAbortReason("cancelled"))
    await owner.release()


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
    invalid_owner._children.append(((0, 0), parent, AwaitingResume((), ()), None))
    await invalid_owner._drive_child(0)

    async def awaiting_terminal() -> tuple[AwaitingResume, AbortedChild, None]:
        return AwaitingResume((), ()), AbortedChild(parent, GraphAbortReason("aborted")), None

    awaiting_owner = root_owner(graph, state)
    awaiting_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            cast(_ChildHandle[str], (awaiting_terminal, no_abort, no_release)),
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
            cast(_ChildHandle[str], (missing_terminal, no_abort, no_release)),
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
            cast(_ChildHandle[str], (completed, no_abort, no_release)),
        )
    )
    with pytest.raises(ResultCollectionError, match="non-completed"):
        await completed_owner._drive_child(0)

    async def aborted() -> tuple[AbortedGraph, CompletedChild[str], ConfirmedChildBoundary[str]]:
        return AbortedGraph(), CompletedChild(parent, output), boundary

    aborted_owner = root_owner(graph, state)
    aborted_owner._children.append(
        (
            (0, 0),
            parent,
            ActiveChild(parent),
            cast(_ChildHandle[str], (aborted, no_abort, no_release)),
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
            cast(_ChildHandle[str], (cancelled_commit, no_abort, no_release)),
        )
    )
    with pytest.raises(asyncio.CancelledError, match="child commit cancelled") as raised:
        await cancelled_owner._drive_child(0)
    assert raised.value is commit_cancellation
    assert cancelled_owner.consume_commit_origin_cancellation(commit_cancellation)


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
        new_evidence_publisher(),
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
        new_evidence_publisher(),
    )
    handle = _opaque_handle(child_owner, parent)

    assert len(handle) == 3
    child_result = await handle[0]()
    assert not isinstance(child_result, asyncio.CancelledError)
    disposition, terminal, boundary = child_result
    assert isinstance(disposition, AbortedGraph)
    assert isinstance(terminal, AbortedChild)
    assert boundary is None
    with pytest.raises(ResultCollectionError, match="only be handed off once"):
        await handle[0]()

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


def test_continuation_transition_plan_requires_a_constructed_owner() -> None:
    graph = compiled_graph("a")
    root_state = running_state(run_id="root")
    foreign_state = leased_state(running_state(run_id="foreign"))
    owner = root_owner(graph, root_state)
    _planned, fences = plan_fences(graph, lineage_states(foreign_state, ()))

    with pytest.raises(SnapshotMismatchError, match="no constructed owner"):
        _validate_owner_transition_plans((owner,), fences, ())


@pytest.mark.asyncio
async def test_child_owner_is_constructed_before_its_resume_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("retry")

    child = Graph[str]("ownership.resume-order.child")
    child.add_node("leaf", fail, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("ownership.resume-order.parent")
    parent.add_node("nested", child, inputs={})
    parent.set_outputs({})
    awaiting = await parent.run(Graph.values())
    assert isinstance(awaiting, Graph.AwaitingResumeResult)
    events: list[tuple[str, tuple[str, ...]]] = []
    original_resume = _GraphRun[str].apply_admission_resume

    async def record_owner_resume(
        self: _GraphRun[str],
        planned: PlannedResume[str],
    ) -> None:
        events.append(("owner", tuple(self.coordinate.scope)))
        await original_resume(self, planned)

    async def record_commit(transition: GraphTransition[str], /) -> GraphRunState:
        if isinstance(transition.command, ResumeGraphNodes):
            events.append(("resume", transition.scope))
        return transition.candidate_state

    monkeypatch.setattr(_GraphRun, "apply_admission_resume", record_owner_resume)
    completed = await parent.run(
        state=awaiting.state,
        continuation=awaiting.continuation,
        resume=(parent.skip_failed("leaf", "skip", scope=("nested",)),),
        commit=record_commit,
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert events.index(("owner", ("nested",))) < events.index(("resume", ("nested",)))


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_commit", [False, True], ids=("error", "cancellation"))
async def test_first_setup_transition_failure_releases_without_aborting(
    cancel_commit: bool,
) -> None:
    class SetupCommitError(RuntimeError):
        pass

    async def fail(_values: Graph.Values[str]) -> Graph.FailureOutcome:
        return Graph.failure("retry")

    child = Graph[str]("ownership.setup-transition-failure.child")
    child.add_node("leaf", fail, inputs={}, outputs={})
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
            resume=(parent.skip_failed("leaf", "skip", scope=("nested",)),),
            commit=reject_resume,
        )

    assert raised.value is original
    assert [type(transition.command) for transition in transitions] == [ResumeGraphNodes]


@pytest.mark.asyncio
async def test_existing_child_admission_rejects_unknown_or_malformed_terminal_bindings() -> None:
    graph, state, _owner, _parent, child_scope, activation, child_state = nested_runtime()
    unknown_parent = StableActivation(root_scope_run(state.run_id), state.superstep, GraphNodeId("unknown"))
    unknown_binding = ChildStateBinding(
        ScopeRunCoordinate((GraphNodeId("unknown"),), GraphRunId("unknown")),
        unknown_parent,
        child_state,
    )
    with pytest.raises(SnapshotMismatchError, match="no parent nested definition"):
        await admit_continuation_root(graph, state, (unknown_binding,))

    malformed = replace(child_state, status=GraphRunStatus.ABORTED, abort=None)
    binding = ChildStateBinding(child_scope, activation, malformed)
    with pytest.raises(SnapshotMismatchError, match="invalid graph state"):
        await admit_continuation_root(graph, state, (binding,))


@pytest.mark.asyncio
async def test_existing_child_admission_validates_exact_current_identity() -> None:
    graph, state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    binding = ChildStateBinding(child_scope, activation, child_state)

    with pytest.raises(SnapshotMismatchError, match="repeats one direct child activation"):
        await admit_continuation_root(graph, state, (binding, binding))

    foreign_parent_scope = root_scope_run(GraphRunId("foreign-parent"))
    foreign_parent = ParentGraphActivation(
        foreign_parent_scope.graph_run_id,
        state.superstep,
        parent.node_id,
    )
    foreign_child_scope = child_scope_run_for_activation(foreign_parent_scope, foreign_parent)
    child_graph = graph.nested_graphs[parent.node_id]
    foreign_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, foreign_child_scope.graph_run_id, foreign_parent),
    )
    foreign_binding = ChildStateBinding(
        foreign_child_scope,
        StableActivation(foreign_parent_scope, foreign_parent.superstep, foreign_parent.node_id),
        foreign_state,
    )
    with pytest.raises(SnapshotMismatchError, match="foreign parent run"):
        await admit_continuation_root(graph, state, (foreign_binding,))

    wrong_coordinate = replace(
        binding,
        coordinate=ScopeRunCoordinate(child_scope.scope, GraphRunId("wrong-coordinate")),
    )
    with pytest.raises(SnapshotMismatchError, match="inconsistent activation coordinates"):
        await admit_continuation_root(graph, state, (wrong_coordinate,))

    wrong_state = replace(binding, state=replace(child_state, run_id=GraphRunId("wrong-state")))
    with pytest.raises(SnapshotMismatchError, match="inconsistent activation coordinates"):
        await admit_continuation_root(graph, state, (wrong_state,))

    wrong_parent = replace(
        binding,
        state=replace(
            child_state,
            parent=ParentGraphActivation(state.run_id, state.superstep, GraphNodeId("other")),
        ),
    )
    with pytest.raises(SnapshotMismatchError, match="inconsistent activation coordinates"):
        await admit_continuation_root(graph, state, (wrong_parent,))

    stale_owner = root_owner(graph, replace(state, superstep=state.superstep + 1))
    with pytest.raises(SnapshotMismatchError, match="not one current pending nested activation"):
        await admit_continuation_root(graph, stale_owner.state, (binding,))


@pytest.mark.asyncio
async def test_existing_child_admission_rejects_a_future_parent_activation() -> None:
    graph, state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    future_parent = ParentGraphActivation(state.run_id, state.superstep + 1, parent.node_id)
    parent_scope = root_scope_run(state.run_id)
    future_scope = child_scope_run_for_activation(parent_scope, future_parent)
    child_graph = graph.nested_graphs[parent.node_id]
    future_state = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, future_scope.graph_run_id, future_parent),
    )
    future_binding = ChildStateBinding(
        future_scope,
        StableActivation(parent_scope, future_parent.superstep, future_parent.node_id),
        future_state,
    )

    coordinate_collision = replace(future_binding, coordinate=child_scope)
    with pytest.raises(SnapshotMismatchError, match="repeats one direct child activation"):
        await admit_continuation_root(
            graph,
            state,
            (ChildStateBinding(child_scope, activation, child_state), coordinate_collision),
        )

    with pytest.raises(SnapshotMismatchError, match="future parent frontier"):
        await admit_continuation_root(
            graph,
            state,
            (future_binding,),
            child_executors=((future_scope, GraphExecutor(child_graph)),),
        )


@pytest.mark.asyncio
async def test_existing_child_setup_cleans_a_constructed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, state, _owner, parent, child_scope, activation, child_state = nested_runtime()
    binding = ChildStateBinding(child_scope, activation, child_state)
    original_admit = family_driver._admit_existing_children
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    class CandidateError(RuntimeError):
        pass

    original = CandidateError("child recursive admission failed")

    async def reject_child(
        candidate: _GraphRun[str],
        candidate_graph: CompiledGraph[str],
        scope_run: ScopeRunCoordinate,
        candidate_state: GraphRunState,
        owner_frames: ScopedFrameIndex[str],
        limits: ExecutionLimits,
        raw_commit: GraphCommit[str] | None,
        evidence_publisher: _EvidencePublisher[str],
        bindings: tuple[ChildStateBinding, ...],
        frames: ScopedFrameIndex[str],
        executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[str]], ...],
    ) -> None:
        if scope_run.scope:
            raise original
        await original_admit(
            candidate,
            candidate_graph,
            scope_run,
            candidate_state,
            owner_frames,
            limits,
            raw_commit,
            evidence_publisher,
            bindings,
            frames,
            executors,
        )

    async def commit(transition: GraphTransition[str], /) -> GraphRunState:
        if transition.scope and isinstance(transition.command, AbortGraphRun):
            cleanup_started.set()
            await cleanup_release.wait()
        return transition.candidate_state

    monkeypatch.setattr(family_driver, "_admit_existing_children", reject_child)
    task = asyncio.create_task(
        admit_continuation_root(
            graph,
            state,
            (binding,),
            child_executors=((child_scope, GraphExecutor(graph.nested_graphs[parent.node_id])),),
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
async def test_unconstructed_child_cleanup_skips_terminal_descendants() -> None:
    graph, state, _owner, _parent, child_scope, activation, child_state = nested_runtime()
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
        await admit_continuation_root(
            graph,
            state,
            (child_binding, grandchild_binding),
        )


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
        await admit_continued_root(
            graph,
            completed,
            (),
            ScopedFrameIndex(),
            (),
            ExecutionLimits(),
            None,
            (),
            (),
            _new_family_identity(),
            recovered=True,
        )

    terminal_child = replace(
        child_state,
        status=GraphRunStatus.COMPLETED,
        frontier=GraphFrontierState(()),
    )
    binding = ChildStateBinding(child_scope, activation, terminal_child)
    with pytest.raises(SnapshotMismatchError, match="no executor"):
        await admit_continued_root(
            graph,
            state,
            (binding,),
            ScopedFrameIndex(),
            (),
            ExecutionLimits(),
            None,
            (),
            (),
            _new_family_identity(),
            recovered=True,
        )


@pytest.mark.asyncio
async def test_abort_preserves_first_child_session_fence_or_state_error() -> None:
    graph, state, _owner, parent, _child_scope, _activation, _child_state = nested_runtime()
    completed = replace(state, status=GraphRunStatus.COMPLETED, frontier=GraphFrontierState(()))

    class CleanupError(RuntimeError):
        pass

    child_error = CleanupError("child abort failed")

    async def no_drive() -> tuple[AwaitingResume, None, None]:
        return AwaitingResume((), ()), None, None

    async def fail_abort(_reason: GraphAbortReason) -> None:
        raise child_error

    async def no_release() -> None:
        return None

    handle = cast(_ChildHandle[str], (no_drive, fail_abort, no_release))
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
        return AwaitingResume((), ()), None, None

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
            cast(_ChildHandle[str], (no_drive, no_abort, fail_release)),
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
            cast(_ChildHandle[str], (no_drive, no_abort, release_on_retry)),
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

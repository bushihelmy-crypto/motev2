from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

from mote_kernel.state.graph_state import (
    AdvanceGraphRun,
    CompleteGraphRun,
    FailGraphRun,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFailure,
    GraphNodeId,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    GraphTaskId,
    ParentGraphTask,
    ResumeGraphRun,
    StartGraphRun,
    SuspendGraphRun,
    reduce_graph_run,
)


def start_command(
    *,
    run_id: str = "run",
    definition_id: str = "graph",
    version: int = 1,
    frontier: tuple[str, ...] = ("b", "a"),
    parent: ParentGraphTask | None = None,
) -> StartGraphRun:
    return StartGraphRun(
        run_id=GraphRunId(run_id),
        definition_id=GraphDefinitionId(definition_id),
        definition_version=GraphDefinitionVersion(version),
        frontier=tuple(GraphNodeId(node_id) for node_id in frontier),
        parent=parent,
    )


def running_state() -> GraphRunState:
    return reduce_graph_run(None, start_command())


def test_start_creates_normalized_immutable_running_state() -> None:
    state = running_state()

    assert state == GraphRunState(
        run_id=GraphRunId("run"),
        definition_id=GraphDefinitionId("graph"),
        definition_version=GraphDefinitionVersion(1),
        status=GraphRunStatus.RUNNING,
        superstep=0,
        frontier=(GraphNodeId("a"), GraphNodeId("b")),
    )
    with pytest.raises(FrozenInstanceError):
        state.superstep = 4  # type: ignore[misc]


def test_start_preserves_valid_parent_linkage() -> None:
    parent = ParentGraphTask(GraphRunId("parent"), GraphTaskId("parent-task"))

    assert reduce_graph_run(None, start_command(parent=parent)).parent is parent


@pytest.mark.parametrize(
    "command",
    [
        start_command(run_id=""),
        start_command(run_id=" run"),
        start_command(definition_id=""),
        start_command(definition_id="graph "),
        start_command(version=0),
        start_command(version=-1),
        start_command(frontier=()),
        start_command(frontier=("a", "a")),
        start_command(frontier=("",)),
        start_command(frontier=(" a",)),
        start_command(parent=ParentGraphTask(GraphRunId("run"), GraphTaskId("task"))),
        start_command(parent=ParentGraphTask(GraphRunId(""), GraphTaskId("task"))),
        start_command(parent=ParentGraphTask(GraphRunId("parent"), GraphTaskId(""))),
    ],
)
def test_invalid_start_fails_closed(command: StartGraphRun) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(None, command)


def test_advance_is_pure_and_guards_expected_superstep() -> None:
    old_state = running_state()
    new_state = reduce_graph_run(
        old_state,
        AdvanceGraphRun(expected_superstep=0, frontier=(GraphNodeId("d"), GraphNodeId("c"))),
    )

    assert old_state.superstep == 0
    assert old_state.frontier == (GraphNodeId("a"), GraphNodeId("b"))
    assert new_state.superstep == 1
    assert new_state.frontier == (GraphNodeId("c"), GraphNodeId("d"))
    with pytest.raises(GraphStateTransitionError, match="stale"):
        reduce_graph_run(new_state, AdvanceGraphRun(0, (GraphNodeId("e"),)))
    with pytest.raises(GraphStateTransitionError, match="stale"):
        reduce_graph_run(new_state, AdvanceGraphRun(2, (GraphNodeId("e"),)))


@pytest.mark.parametrize("frontier", [(), (GraphNodeId("a"), GraphNodeId("a")), (GraphNodeId(" a"),)])
def test_advance_rejects_invalid_frontier(frontier: tuple[GraphNodeId, ...]) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(running_state(), AdvanceGraphRun(0, frontier))


def test_suspend_and_resume_preserve_committed_position() -> None:
    running = running_state()
    suspended = reduce_graph_run(running, SuspendGraphRun())
    resumed = reduce_graph_run(suspended, ResumeGraphRun())

    assert suspended.status is GraphRunStatus.SUSPENDED
    assert suspended.frontier == running.frontier
    assert resumed == running


def test_complete_clears_frontier_and_guards_expected_superstep() -> None:
    state = running_state()

    with pytest.raises(GraphStateTransitionError, match="stale"):
        reduce_graph_run(state, CompleteGraphRun(1))
    with pytest.raises(GraphStateTransitionError, match="stale"):
        reduce_graph_run(state, CompleteGraphRun(-1))
    completed = reduce_graph_run(state, CompleteGraphRun(0))
    assert completed.status is GraphRunStatus.COMPLETED
    assert completed.frontier == ()


@pytest.mark.parametrize("suspend_first", [False, True])
def test_fail_clears_frontier_from_nonterminal_state(suspend_first: bool) -> None:
    state = running_state()
    if suspend_first:
        state = reduce_graph_run(state, SuspendGraphRun())

    failed = reduce_graph_run(state, FailGraphRun(GraphFailure("node failed")))

    assert failed.status is GraphRunStatus.FAILED
    assert failed.failure == GraphFailure("node failed")
    assert failed.frontier == ()


def test_empty_failure_fails_closed() -> None:
    for failure in (GraphFailure(""), GraphFailure("  ")):
        with pytest.raises(GraphStateTransitionError):
            reduce_graph_run(running_state(), FailGraphRun(failure))


def test_non_start_command_requires_existing_run() -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(None, SuspendGraphRun())


def test_existing_run_cannot_start_again() -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(running_state(), start_command())


def corrupted_state(**changes: object) -> GraphRunState:
    state = running_state()
    for field, value in changes.items():
        object.__setattr__(state, field, value)
    return state


@pytest.mark.parametrize(
    "state",
    [
        corrupted_state(run_id=GraphRunId("")),
        corrupted_state(run_id=GraphRunId(" run")),
        corrupted_state(definition_id=GraphDefinitionId("")),
        corrupted_state(definition_id=GraphDefinitionId("graph ")),
        corrupted_state(definition_version=GraphDefinitionVersion(0)),
        corrupted_state(superstep=-1),
        corrupted_state(frontier=()),
        corrupted_state(frontier=(GraphNodeId("a"), GraphNodeId("a"))),
        corrupted_state(frontier=(GraphNodeId(" a"),)),
        corrupted_state(parent=ParentGraphTask(GraphRunId(""), GraphTaskId("task"))),
        corrupted_state(parent=ParentGraphTask(GraphRunId("parent"), GraphTaskId(""))),
        corrupted_state(parent=ParentGraphTask(GraphRunId("run"), GraphTaskId("task"))),
        corrupted_state(failure=GraphFailure("unexpected")),
        corrupted_state(status=GraphRunStatus.COMPLETED, frontier=(GraphNodeId("a"),)),
        corrupted_state(status=GraphRunStatus.FAILED, frontier=(), failure=None),
        corrupted_state(status=GraphRunStatus.FAILED, frontier=(), failure=GraphFailure("  ")),
    ],
)
def test_corrupt_recovered_state_fails_closed_before_transition(state: GraphRunState) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state, SuspendGraphRun())


def test_valid_recovered_failed_state_reaches_lifecycle_guard() -> None:
    state = corrupted_state(status=GraphRunStatus.FAILED, frontier=(), failure=GraphFailure("failed"))

    with pytest.raises(GraphStateTransitionError, match="terminal"):
        reduce_graph_run(state, FailGraphRun(GraphFailure("again")))


def test_valid_recovered_parent_linkage_can_transition() -> None:
    parent = ParentGraphTask(GraphRunId("parent"), GraphTaskId("parent-task"))
    state = corrupted_state(parent=parent)

    suspended = reduce_graph_run(state, SuspendGraphRun())

    assert suspended.parent is parent


def test_valid_recovered_completed_state_reaches_lifecycle_guard() -> None:
    state = corrupted_state(status=GraphRunStatus.COMPLETED, frontier=())

    with pytest.raises(GraphStateTransitionError, match="running"):
        reduce_graph_run(state, SuspendGraphRun())


@pytest.mark.parametrize(
    ("state_factory", "command"),
    [
        (lambda: reduce_graph_run(running_state(), SuspendGraphRun()), AdvanceGraphRun(0, (GraphNodeId("c"),))),
        (lambda: reduce_graph_run(running_state(), SuspendGraphRun()), SuspendGraphRun()),
        (running_state, ResumeGraphRun()),
        (lambda: reduce_graph_run(running_state(), SuspendGraphRun()), CompleteGraphRun(0)),
        (lambda: reduce_graph_run(running_state(), CompleteGraphRun(0)), AdvanceGraphRun(0, (GraphNodeId("c"),))),
        (lambda: reduce_graph_run(running_state(), CompleteGraphRun(0)), SuspendGraphRun()),
        (lambda: reduce_graph_run(running_state(), CompleteGraphRun(0)), ResumeGraphRun()),
        (lambda: reduce_graph_run(running_state(), CompleteGraphRun(0)), CompleteGraphRun(0)),
        (lambda: reduce_graph_run(running_state(), CompleteGraphRun(0)), FailGraphRun(GraphFailure("late"))),
        (
            lambda: reduce_graph_run(running_state(), FailGraphRun(GraphFailure("failed"))),
            AdvanceGraphRun(0, (GraphNodeId("c"),)),
        ),
        (lambda: reduce_graph_run(running_state(), FailGraphRun(GraphFailure("failed"))), SuspendGraphRun()),
        (lambda: reduce_graph_run(running_state(), FailGraphRun(GraphFailure("failed"))), ResumeGraphRun()),
        (lambda: reduce_graph_run(running_state(), FailGraphRun(GraphFailure("failed"))), CompleteGraphRun(0)),
        (
            lambda: reduce_graph_run(running_state(), FailGraphRun(GraphFailure("failed"))),
            FailGraphRun(GraphFailure("again")),
        ),
    ],
)
def test_illegal_lifecycle_transitions_fail_closed(
    state_factory: Callable[[], GraphRunState], command: GraphRunCommand
) -> None:
    with pytest.raises(GraphStateTransitionError):
        reduce_graph_run(state_factory(), command)

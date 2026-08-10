from dataclasses import FrozenInstanceError

import pytest
from tests.execution.engine.factories import compiled_graph, snapshot

from mote_kernel.execution.engine import GraphTask, plan_tasks
from mote_kernel.execution.engine.collector import CollectedResults, collect_results
from mote_kernel.execution.engine.task import TaskId
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import NodeId
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskSuccess
from mote_kernel.execution.snapshot import ExecutionStatus, GraphRunId


def test_results_are_collected_in_task_identity_order_not_arrival_order() -> None:
    execution_snapshot = snapshot(frontier=("a", "b"))
    tasks = plan_tasks(compiled_graph("a", "b", entries=("a", "b")), execution_snapshot, ExecutionLimits())
    results = (TaskSuccess(tasks[1], "b-output"), TaskSuccess(tasks[0], "a-output"))

    collected = collect_results(execution_snapshot, tasks, results)

    assert tuple(result.task.task_id for result in collected.successes) == tuple(sorted(task.task_id for task in tasks))
    assert collected.failure is None


def test_collected_results_are_immutable() -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a"), execution_snapshot, ExecutionLimits())[0]
    collected = collect_results(execution_snapshot, (task,), (TaskSuccess(task, "output"),))

    with pytest.raises(FrozenInstanceError):
        collected.failure = TaskFailure(task, "failed")  # type: ignore[misc]


@pytest.mark.parametrize("status", [ExecutionStatus.SUSPENDED, ExecutionStatus.COMPLETED, ExecutionStatus.FAILED])
def test_results_cannot_settle_nonrunning_snapshot(status: ExecutionStatus) -> None:
    with pytest.raises(ResultCollectionError):
        collect_results(snapshot(status=status, frontier=()), (), ())


def test_failure_wins_deterministically_by_task_identity() -> None:
    execution_snapshot = snapshot(frontier=("a", "b"))
    tasks = plan_tasks(compiled_graph("a", "b", entries=("a", "b")), execution_snapshot, ExecutionLimits())
    results = (TaskFailure(tasks[1], "second"), TaskFailure(tasks[0], "first"))

    collected: CollectedResults[str] = collect_results(execution_snapshot, tasks, results)

    assert collected.successes == ()
    assert collected.failure == TaskFailure(tasks[0], "first")


def test_mixed_success_and_failures_discard_successes_and_choose_failure_stably() -> None:
    execution_snapshot = snapshot(frontier=("a", "b", "c"))
    tasks = plan_tasks(
        compiled_graph("a", "b", "c", entries=("a", "b", "c")),
        execution_snapshot,
        ExecutionLimits(),
    )
    results = (
        TaskFailure(tasks[2], "third failed"),
        TaskSuccess(tasks[1], "second succeeded"),
        TaskFailure(tasks[0], "first failed"),
    )

    collected: CollectedResults[str] = collect_results(execution_snapshot, tasks, results)

    assert collected == CollectedResults((), TaskFailure(tasks[0], "first failed"))


def test_every_failure_payload_is_validated_before_one_is_selected() -> None:
    execution_snapshot = snapshot(frontier=("a", "b"))
    tasks = plan_tasks(compiled_graph("a", "b", entries=("a", "b")), execution_snapshot, ExecutionLimits())

    with pytest.raises(ResultCollectionError, match="failure"):
        collect_results(
            execution_snapshot,
            tasks,
            (TaskFailure(tasks[0], "valid"), TaskFailure(tasks[1], "  ")),
        )


@pytest.mark.parametrize("output", [None, False, 0, "", ()])
def test_falsy_success_outputs_are_preserved(output: object) -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a"), execution_snapshot, ExecutionLimits())[0]

    collected = collect_results(execution_snapshot, (task,), (TaskSuccess(task, output),))

    assert collected.successes[0].output == output


@pytest.mark.parametrize("missing_results", [(), None])
def test_missing_result_fails_closed(missing_results: tuple[TaskSuccess[str], ...] | None) -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a"), execution_snapshot, ExecutionLimits())[0]
    results = () if missing_results is None else missing_results

    with pytest.raises(ResultCollectionError, match="exactly one"):
        collect_results(execution_snapshot, (task,), results)


def test_duplicate_result_fails_closed() -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a"), execution_snapshot, ExecutionLimits())[0]
    result = TaskSuccess(task, "output")

    with pytest.raises(ResultCollectionError, match="more than one"):
        collect_results(execution_snapshot, (task,), (result, result))


def test_unknown_result_fails_closed() -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a"), execution_snapshot, ExecutionLimits())[0]
    unknown = GraphTask(TaskId("unknown"), task.run_id, task.superstep, task.node_id)

    with pytest.raises(ResultCollectionError, match="unknown"):
        collect_results(execution_snapshot, (task,), (TaskSuccess(unknown, "output"),))


@pytest.mark.parametrize(
    "foreign_task",
    [
        GraphTask(TaskId("task"), GraphRunId("other"), 0, NodeId("a")),
        GraphTask(TaskId("task"), GraphRunId("run"), 1, NodeId("a")),
    ],
)
def test_planned_task_must_belong_to_snapshot(foreign_task: GraphTask) -> None:
    with pytest.raises(ResultCollectionError, match="snapshot"):
        collect_results(snapshot(), (foreign_task,), ())


def test_duplicate_planned_task_identity_fails_closed() -> None:
    execution_snapshot = snapshot(frontier=("a", "b"))
    tasks = plan_tasks(compiled_graph("a", "b", entries=("a", "b")), execution_snapshot, ExecutionLimits())
    duplicate_id = GraphTask(tasks[0].task_id, tasks[1].run_id, tasks[1].superstep, tasks[1].node_id)

    with pytest.raises(ResultCollectionError, match="unique"):
        collect_results(execution_snapshot, (tasks[0], duplicate_id), ())


def test_planned_tasks_must_cover_entire_snapshot_frontier() -> None:
    execution_snapshot = snapshot(frontier=("a", "b"))
    tasks = plan_tasks(compiled_graph("a", "b", entries=("a", "b")), execution_snapshot, ExecutionLimits())

    with pytest.raises(ResultCollectionError, match="exactly cover"):
        collect_results(execution_snapshot, (tasks[0],), (TaskSuccess(tasks[0], "output"),))


def test_planned_task_identity_must_be_canonical() -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a"), execution_snapshot, ExecutionLimits())[0]
    changed = GraphTask(TaskId("non-canonical"), task.run_id, task.superstep, task.node_id)

    with pytest.raises(ResultCollectionError, match="committed coordinates"):
        collect_results(execution_snapshot, (changed,), (TaskSuccess(changed, "output"),))


@pytest.mark.parametrize("failure", ["", "  ", " failure"])
def test_task_failure_requires_stable_nonempty_reason(failure: str) -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a"), execution_snapshot, ExecutionLimits())[0]

    with pytest.raises(ResultCollectionError, match="failure"):
        collect_results(execution_snapshot, (task,), (TaskFailure(task, failure),))


def test_same_identity_with_changed_coordinates_fails_closed() -> None:
    execution_snapshot = snapshot()
    task = plan_tasks(compiled_graph("a", "b", entries=("a", "b")), execution_snapshot, ExecutionLimits())[0]
    changed = GraphTask(task.task_id, task.run_id, task.superstep, NodeId("b"))

    with pytest.raises(ResultCollectionError, match="coordinates"):
        collect_results(execution_snapshot, (task,), (TaskSuccess(changed, "output"),))


def test_result_cannot_substitute_a_different_canonical_task() -> None:
    execution_snapshot = snapshot(frontier=("a", "b"))
    tasks = plan_tasks(compiled_graph("a", "b", entries=("a", "b")), execution_snapshot, ExecutionLimits())
    substituted = GraphTask(tasks[0].task_id, tasks[1].run_id, tasks[1].superstep, tasks[1].node_id)

    with pytest.raises(ResultCollectionError, match="coordinates"):
        collect_results(
            execution_snapshot,
            tasks,
            (TaskSuccess(substituted, "wrong"), TaskSuccess(tasks[1], "right")),
        )

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest
from tests.execution.engine.factories import compiled_graph, running_state, terminal_state

from mote_kernel.execution.engine.collector import CollectedResults, collect_results
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import GraphNodeId
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskResult, TaskSuccess
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    GraphFailure,
    GraphInterruptPayload,
    GraphRunId,
    GraphRunStatus,
)


def planned(count: int = 3):
    node_ids = tuple(chr(ord("a") + index) for index in range(count))
    state = running_state(frontier=node_ids)
    graph = compiled_graph(*node_ids, entries=node_ids)
    return state, plan_tasks(graph, state, ExecutionLimits())


def test_collector_preserves_all_typed_outcomes_in_planned_order() -> None:
    state, tasks = planned()
    results = (
        TaskInterrupt(tasks[2], GraphInterruptPayload(b"question")),
        TaskSuccess(tasks[0], "output", ContinueGraphRouting()),
        TaskFailure(tasks[1], GraphFailure("failed")),
    )

    collected = collect_results(state, tasks, results)

    assert collected == CollectedResults(
        (TaskSuccess(tasks[0], "output", ContinueGraphRouting()),),
        (TaskFailure(tasks[1], GraphFailure("failed")),),
        (TaskInterrupt(tasks[2], GraphInterruptPayload(b"question")),),
    )
    with pytest.raises(FrozenInstanceError):
        collected.failures = ()  # type: ignore[misc]


def test_results_are_collected_in_planned_identity_order_not_arrival_order() -> None:
    state, tasks = planned(3)
    collected = collect_results(
        state,
        tasks,
        tuple(TaskSuccess(task, f"{task.node_id}-output", ContinueGraphRouting()) for task in reversed(tasks)),
    )

    assert tuple(result.task.task_id for result in collected.successes) == tuple(task.task_id for task in tasks)


def test_collected_results_preserve_every_failure_in_canonical_order() -> None:
    state, tasks = planned(3)
    collected = collect_results(
        state,
        tasks,
        (
            TaskFailure(tasks[2], GraphFailure("third")),
            TaskSuccess(tasks[1], "second", ContinueGraphRouting()),
            TaskFailure(tasks[0], GraphFailure("first")),
        ),
    )

    assert collected.successes == (TaskSuccess(tasks[1], "second", ContinueGraphRouting()),)
    assert collected.failures == (
        TaskFailure(tasks[0], GraphFailure("first")),
        TaskFailure(tasks[2], GraphFailure("third")),
    )


@pytest.mark.parametrize("status", [GraphRunStatus.COMPLETED, GraphRunStatus.ABORTED])
def test_results_cannot_settle_terminal_state(status: GraphRunStatus) -> None:
    with pytest.raises(ResultCollectionError, match="running"):
        collect_results(terminal_state(status), (), ())


@pytest.mark.parametrize("results", [(), None])
def test_missing_result_fails_closed(results: tuple[TaskSuccess[str], ...] | None) -> None:
    state, tasks = planned(1)
    supplied = () if results is None else results
    with pytest.raises(ResultCollectionError, match="exactly one"):
        collect_results(state, tasks, supplied)


@pytest.mark.parametrize("case", ["duplicate", "unknown", "unsupported"])
def test_duplicate_unknown_and_unsupported_results_fail_closed(case: str) -> None:
    state, tasks = planned(1)
    result = TaskSuccess(tasks[0], "output", ContinueGraphRouting())
    forged = GraphTask(TaskId("forged"), tasks[0].run_id, tasks[0].superstep, tasks[0].node_id)
    supplied: tuple[TaskResult[str], ...] = {
        "duplicate": (result, result),
        "unknown": (TaskSuccess(forged, "output", ContinueGraphRouting()),),
        "unsupported": (cast(TaskResult[str], object()),),
    }[case]
    message = "unsupported variant" if case == "unsupported" else "uniquely"
    with pytest.raises(ResultCollectionError, match=message):
        collect_results(state, tasks, supplied)


@pytest.mark.parametrize("case", ["forged-identity", "foreign-run", "foreign-superstep"])
def test_planned_tasks_must_have_canonical_coordinates(case: str) -> None:
    state, tasks = planned(2)
    forged = {
        "forged-identity": GraphTask(
            TaskId("forged"),
            tasks[0].run_id,
            tasks[0].superstep,
            tasks[0].node_id,
        ),
        "foreign-run": GraphTask(
            tasks[0].task_id,
            GraphRunId("other"),
            tasks[0].superstep,
            tasks[0].node_id,
        ),
        "foreign-superstep": GraphTask(
            tasks[0].task_id,
            tasks[0].run_id,
            tasks[0].superstep + 1,
            tasks[0].node_id,
        ),
    }[case]
    with pytest.raises(ResultCollectionError, match="coordinates"):
        collect_results(state, (forged, tasks[1]), ())


@pytest.mark.parametrize(
    "foreign_task",
    [
        GraphTask(TaskId("task"), GraphRunId("other"), 0, GraphNodeId("a")),
        GraphTask(TaskId("task"), GraphRunId("run"), 1, GraphNodeId("a")),
    ],
)
def test_planned_task_must_belong_to_current_run_activation(foreign_task: GraphTask) -> None:
    with pytest.raises(ResultCollectionError, match="coordinates"):
        collect_results(running_state(), (foreign_task,), ())


def test_planned_tasks_must_cover_entire_pending_frontier() -> None:
    state, tasks = planned(2)
    with pytest.raises(ResultCollectionError, match="exactly cover"):
        collect_results(state, (tasks[0],), (TaskSuccess(tasks[0], "output", ContinueGraphRouting()),))


def test_planned_task_identity_must_be_canonical() -> None:
    state, tasks = planned(1)
    changed = replace(tasks[0], task_id=TaskId("non-canonical"))
    with pytest.raises(ResultCollectionError, match="coordinates"):
        collect_results(state, (changed,), ())


def test_same_task_identity_with_changed_coordinates_fails_closed() -> None:
    state, tasks = planned(2)
    changed = replace(tasks[0], node_id=tasks[1].node_id)
    with pytest.raises(ResultCollectionError, match="uniquely"):
        collect_results(
            state,
            tasks,
            (
                TaskSuccess(changed, "wrong", ContinueGraphRouting()),
                TaskSuccess(tasks[1], "right", ContinueGraphRouting()),
            ),
        )


def test_result_cannot_substitute_a_different_canonical_task() -> None:
    state, tasks = planned(2)
    substituted = replace(tasks[1], task_id=tasks[0].task_id)

    with pytest.raises(ResultCollectionError, match="uniquely"):
        collect_results(
            state,
            tasks,
            (
                TaskSuccess(substituted, "wrong", ContinueGraphRouting()),
                TaskSuccess(tasks[1], "right", ContinueGraphRouting()),
            ),
        )


def test_collector_rejects_duplicate_planned_task_identity() -> None:
    state, tasks = planned(2)
    duplicate = replace(tasks[1], task_id=tasks[0].task_id)
    with pytest.raises(ResultCollectionError, match="duplicate"):
        collect_results(state, (tasks[0], duplicate), ())


@pytest.mark.parametrize("output", [None, False, 0, "", ()])
def test_falsy_success_output_is_transient_and_preserved(output: object) -> None:
    state, tasks = planned(1)
    collected = collect_results(
        state,
        tasks,
        (TaskSuccess(tasks[0], output, ContinueGraphRouting()),),
    )
    assert collected.successes[0].output == output

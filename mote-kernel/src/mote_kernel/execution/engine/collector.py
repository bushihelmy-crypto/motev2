"""Deterministic node-result collection and conflict detection."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.task import GraphTask, TaskId, task_identity
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.result import TaskFailure, TaskResult, TaskSuccess
from mote_kernel.execution.snapshot import ExecutionSnapshot, ExecutionStatus

OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class CollectedResults(Generic[OutputT]):
    """One complete task batch normalized into stable identity order."""

    successes: tuple[TaskSuccess[OutputT], ...]
    failure: TaskFailure | None = None


def collect_results(
    snapshot: ExecutionSnapshot,
    planned_tasks: tuple[GraphTask, ...],
    results: tuple[TaskResult[OutputT], ...],
) -> CollectedResults[OutputT]:
    """Validate and deterministically collect exactly one planned result per task."""

    if snapshot.status is not ExecutionStatus.RUNNING:
        raise ResultCollectionError("task results can only settle a running graph")
    expected: dict[TaskId, GraphTask] = {}
    for task in planned_tasks:
        if task.run_id != snapshot.run_id or task.superstep != snapshot.superstep:
            raise ResultCollectionError("planned task does not belong to the snapshot superstep")
        if task.task_id in expected:
            raise ResultCollectionError("planned task identities must be unique")
        expected[task.task_id] = task
    expected_nodes = tuple(sorted(snapshot.frontier))
    planned_nodes = tuple(sorted(task.node_id for task in planned_tasks))
    if planned_nodes != expected_nodes:
        raise ResultCollectionError("planned tasks must exactly cover the snapshot frontier")
    if any(task.task_id != task_identity(task.run_id, task.superstep, task.node_id) for task in planned_tasks):
        raise ResultCollectionError("planned task identity does not match its committed coordinates")
    received: dict[TaskId, TaskResult[OutputT]] = {}
    for result in results:
        task = result.task
        expected_task = expected.get(task.task_id)
        if expected_task is None:
            raise ResultCollectionError("received a result for an unknown task")
        if task != expected_task:
            raise ResultCollectionError("result task coordinates do not match the planned task")
        if task.task_id in received:
            raise ResultCollectionError("received more than one result for a task")
        received[task.task_id] = result
    if received.keys() != expected.keys():
        raise ResultCollectionError("every planned task must have exactly one result")
    ordered = tuple(received[task_id] for task_id in sorted(received))
    failures = tuple(result for result in ordered if isinstance(result, TaskFailure))
    if failures:
        for failure in failures:
            if (
                not failure.failure
                or failure.failure != failure.failure.strip()
                or "\n" in failure.failure
                or "\r" in failure.failure
            ):
                raise ResultCollectionError("task failure must be non-empty and trimmed")
        return CollectedResults(successes=(), failure=failures[0])
    successes = tuple(result for result in ordered if isinstance(result, TaskSuccess))
    return CollectedResults(successes=successes)


__all__: list[str] = []

"""Deterministic complete typed-result collection."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.task import GraphTask, TaskId, task_identity
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskResult, TaskSuccess
from mote_kernel.state.graph_state import GraphRunState, GraphRunStatus, pending_node_ids

OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class CollectedResults(Generic[OutputT]):
    successes: tuple[TaskSuccess[OutputT], ...]
    failures: tuple[TaskFailure, ...]
    interrupts: tuple[TaskInterrupt, ...]


def collect_results(
    state: GraphRunState,
    planned_tasks: tuple[GraphTask, ...],
    results: tuple[TaskResult[OutputT], ...],
) -> CollectedResults[OutputT]:
    if state.status is not GraphRunStatus.RUNNING:
        raise ResultCollectionError("task results can only settle a running graph")
    expected: dict[TaskId, GraphTask] = {}
    for task in planned_tasks:
        if (
            task.run_id != state.run_id
            or task.superstep != state.superstep
            or task.task_id != task_identity(task.run_id, task.superstep, task.node_id)
            or task.task_id in expected
        ):
            raise ResultCollectionError("planned task has invalid or duplicate coordinates")
        expected[task.task_id] = task
    if tuple(task.node_id for task in planned_tasks) != pending_node_ids(state.frontier):
        raise ResultCollectionError("planned tasks must exactly cover current pending nodes")
    received: dict[TaskId, TaskResult[OutputT]] = {}
    for result in results:
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            result, TaskSuccess | TaskFailure | TaskInterrupt
        ):
            raise ResultCollectionError("task result has an unsupported variant")
        expected_task = expected.get(result.task.task_id)
        if expected_task != result.task or result.task.task_id in received:
            raise ResultCollectionError("result does not uniquely match a planned task")
        received[result.task.task_id] = result
    if received.keys() != expected.keys():
        raise ResultCollectionError("every planned task must have exactly one result")
    ordered = tuple(received[task_id] for task_id in expected)
    return CollectedResults(
        tuple(result for result in ordered if isinstance(result, TaskSuccess)),
        tuple(result for result in ordered if isinstance(result, TaskFailure)),
        tuple(result for result in ordered if isinstance(result, TaskInterrupt)),
    )


__all__ = ["CollectedResults", "collect_results"]

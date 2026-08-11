"""Async task invocation with deterministic result collection."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar

from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import NodeExecutionContractError, ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph, NestedGraphNodeDefinition, NodeFailure, NodeSuccess
from mote_kernel.execution.result import NestedTaskFailure, NestedTaskResult, TaskFailure, TaskResult, TaskSuccess

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class _TaskRaised:
    """An ordinary node exception retained until its whole batch settles."""

    error: Exception


async def _execute_task(
    graph: CompiledGraph[InputT, OutputT],
    task: GraphTask,
    node_input: InputT,
    nested_by_task: Mapping[TaskId, NestedTaskResult[OutputT]],
) -> TaskResult[OutputT]:
    definition = graph.nodes[task.node_id]
    if isinstance(definition, NestedGraphNodeDefinition):
        nested = nested_by_task[task.task_id]
        if isinstance(nested, NestedTaskFailure):
            return TaskFailure(task, nested.failure)
        return TaskSuccess(task, nested.output, nested.routing)
    match await definition.node(node_input):
        case NodeSuccess(output=output, routing=routing):
            return TaskSuccess(task, output, routing)
        case NodeFailure(failure=failure):
            return TaskFailure(task, failure)
        case invalid:
            raise NodeExecutionContractError(f"node returned an invalid outcome: {type(invalid).__name__}")


async def _capture_task_exception(
    graph: CompiledGraph[InputT, OutputT],
    task: GraphTask,
    node_input: InputT,
    nested_by_task: Mapping[TaskId, NestedTaskResult[OutputT]],
) -> TaskResult[OutputT] | _TaskRaised:
    try:
        return await _execute_task(graph, task, node_input, nested_by_task)
    except Exception as error:
        return _TaskRaised(error)


async def execute_tasks(
    graph: CompiledGraph[InputT, OutputT],
    tasks: tuple[GraphTask, ...],
    node_input: InputT,
    nested_results: tuple[NestedTaskResult[OutputT], ...] = (),
) -> tuple[TaskResult[OutputT], ...]:
    """Invoke a concurrent batch with one shared immutable input and collect results in task order."""

    nested_by_task = {result.task_id: result for result in nested_results}
    nested_task_ids = {
        task.task_id for task in tasks if isinstance(graph.nodes[task.node_id], NestedGraphNodeDefinition)
    }
    if set(nested_by_task) != nested_task_ids:
        raise ResultCollectionError("nested task results must exactly cover planned nested graph tasks")
    if not tasks:
        return ()

    async with asyncio.TaskGroup() as task_group:
        scheduled = tuple(
            task_group.create_task(
                _capture_task_exception(
                    graph,
                    task,
                    node_input,
                    nested_by_task,
                ),
                name=f"mote-graph:{task.task_id}",
            )
            for task in tasks
        )

    results: list[TaskResult[OutputT]] = []
    for scheduled_task in scheduled:
        outcome = scheduled_task.result()
        if isinstance(outcome, _TaskRaised):
            raise outcome.error
        results.append(outcome)
    return tuple(results)


__all__: list[str] = []

"""Async task invocation with deterministic typed outcome collection."""

import asyncio
from dataclasses import dataclass
from typing import TypeVar

from mote_kernel.execution.engine.task import ExecutableTask
from mote_kernel.execution.errors import NodeExecutionContractError
from mote_kernel.execution.graph import (
    CompiledGraph,
    NestedGraphNodeDefinition,
    NodeFailure,
    NodeInterrupt,
    NodeSuccess,
)
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskResult, TaskSuccess

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class _TaskRaised:
    error: Exception


async def _execute_task(
    graph: CompiledGraph[InputT, OutputT], executable: ExecutableTask[InputT]
) -> TaskResult[OutputT]:
    task = executable.task
    definition = graph.nodes[task.node_id]
    if isinstance(definition, NestedGraphNodeDefinition):
        raise NodeExecutionContractError("nested task must be projected to an executable terminal outcome")
    outcome = await definition.node(executable.effective_input)
    if isinstance(outcome, NodeSuccess):
        return TaskSuccess(task, outcome.output, outcome.routing)
    if isinstance(outcome, NodeFailure):
        return TaskFailure(task, outcome.failure)
    if isinstance(outcome, NodeInterrupt):  # pyright: ignore[reportUnnecessaryIsInstance]
        return TaskInterrupt(task, outcome.request_payload)
    raise NodeExecutionContractError("graph node returned an unsupported outcome")


async def _capture(
    graph: CompiledGraph[InputT, OutputT], executable: ExecutableTask[InputT]
) -> TaskResult[OutputT] | _TaskRaised:
    try:
        return await _execute_task(graph, executable)
    except Exception as error:
        return _TaskRaised(error)


async def execute_tasks(
    graph: CompiledGraph[InputT, OutputT], executables: tuple[ExecutableTask[InputT], ...]
) -> tuple[TaskResult[OutputT], ...]:
    if not executables:
        return ()
    async with asyncio.TaskGroup() as group:
        scheduled = tuple(
            group.create_task(_capture(graph, executable), name=f"mote-graph:{executable.task.task_id}")
            for executable in executables
        )
    results: list[TaskResult[OutputT]] = []
    for scheduled_task in scheduled:
        outcome = scheduled_task.result()
        if isinstance(outcome, _TaskRaised):
            raise outcome.error
        results.append(outcome)
    return tuple(results)


__all__ = ["execute_tasks"]

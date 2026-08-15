"""The single dynamic async task pool used by graph execution."""

import asyncio
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.task import ExecutableTask, GraphTask, TaskId
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
class TaskRaised:
    task: GraphTask
    error: Exception


async def _execute_task(
    graph: CompiledGraph[InputT, OutputT], executable: ExecutableTask[InputT]
) -> TaskResult[OutputT]:
    definition = graph.nodes[executable.task.node_id]
    if isinstance(definition, NestedGraphNodeDefinition):
        raise NodeExecutionContractError("nested task must be projected to a precomputed terminal outcome")
    outcome = await definition.node(executable.effective_input)
    if isinstance(outcome, NodeSuccess):
        return TaskSuccess(executable.task, outcome.output, outcome.routing)
    if isinstance(outcome, NodeFailure):
        return TaskFailure(executable.task, outcome.failure)
    if isinstance(outcome, NodeInterrupt):  # pyright: ignore[reportUnnecessaryIsInstance]
        return TaskInterrupt(executable.task, outcome.request_payload)
    raise NodeExecutionContractError("graph node returned an unsupported outcome")


async def _capture(
    graph: CompiledGraph[InputT, OutputT], executable: ExecutableTask[InputT]
) -> TaskResult[OutputT] | TaskRaised:
    try:
        return await _execute_task(graph, executable)
    except Exception as error:
        return TaskRaised(executable.task, error)


class TaskScheduler(Generic[InputT, OutputT]):
    """Submit ordinary node tasks and yield one completion at a time."""

    __slots__ = ("_events", "_graph", "_live")

    def __init__(self, graph: CompiledGraph[InputT, OutputT]) -> None:
        self._graph = graph
        self._live: dict[TaskId, tuple[ExecutableTask[InputT], asyncio.Task[TaskResult[OutputT] | TaskRaised]]] = {}
        self._events: list[TaskResult[OutputT] | TaskRaised] = []

    @property
    def live_count(self) -> int:
        return len(self._live)

    @property
    def has_pending_events(self) -> bool:
        return bool(self._events)

    def take_pending_errors(self) -> tuple[TaskRaised, ...]:
        """Remove ready ordinary errors while preserving typed completion order."""

        errors = tuple(event for event in self._events if isinstance(event, TaskRaised))
        if errors:
            self._events = [event for event in self._events if not isinstance(event, TaskRaised)]
        return errors

    def submit(self, executables: tuple[ExecutableTask[InputT], ...]) -> None:
        task_ids = tuple(executable.task.task_id for executable in executables)
        existing = set(self._live)
        existing.update(event.task.task_id for event in self._events)
        if len(task_ids) != len(set(task_ids)) or existing.intersection(task_ids):
            raise NodeExecutionContractError("a graph task was submitted more than once")
        for executable in executables:
            task_id = executable.task.task_id
            handle = asyncio.create_task(_capture(self._graph, executable), name=f"mote-graph:{task_id}")
            self._live[task_id] = (executable, handle)

    async def next_completion(self) -> TaskResult[OutputT] | TaskRaised:
        if self._events:
            return self._events.pop(0)
        if not self._live:
            raise NodeExecutionContractError("there are no live graph tasks")
        by_handle = {handle: executable for executable, handle in self._live.values()}
        done, _pending = await asyncio.wait(
            tuple(by_handle),
            return_when=asyncio.FIRST_COMPLETED,
        )
        events: list[tuple[tuple[int, str, TaskId], TaskResult[OutputT] | TaskRaised]] = []
        for handle in done:
            executable = by_handle[handle]
            self._live.pop(executable.task.task_id, None)
            event = handle.result()
            events.append((executable.task.sort_key, event))
        events.sort(key=lambda item: item[0])
        self._events.extend(event for _key, event in events[1:])
        return events[0][1]

    async def aclose(self) -> None:
        handles = tuple(handle for _executable, handle in self._live.values())
        for handle in handles:
            handle.cancel()
        if handles:
            await asyncio.gather(*handles, return_exceptions=True)
        self._live.clear()
        self._events.clear()


__all__ = ["TaskRaised", "TaskScheduler"]

"""The single dynamic async task pool used by graph execution."""

import asyncio
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.engine.routing import validate_routing_contribution
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask, TaskId
from mote_kernel.execution.errors import NodeExecutionContractError
from mote_kernel.execution.graph.definition import NestedGraphNodeDefinition
from mote_kernel.execution.graph.outcome import (
    _GraphFailureOutcome,
    _GraphInterruptOutcome,
    _GraphSuccessOutcome,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    _GraphValues,
    _make_node_output_frame,
    _public_node_input,
)
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskResult, TaskSuccess
from mote_kernel.state.graph_state import ContinueGraphRouting, GraphRouteId, SelectGraphRoute

GraphValueT = TypeVar("GraphValueT")
NodeReturn: TypeAlias = (
    _GraphValues[GraphValueT] | _GraphSuccessOutcome[GraphValueT] | _GraphFailureOutcome | _GraphInterruptOutcome
)


@dataclass(frozen=True, slots=True)
class TaskRaised:
    task: GraphTask
    error: BaseException


_SCHEDULER_CLOSE_CANCEL = object()


def cancel_scheduler_task(task: asyncio.Task[TaskResult[GraphValueT] | TaskRaised]) -> None:
    task.cancel(_SCHEDULER_CLOSE_CANCEL)


def _project_outcome(
    graph: CompiledGraph[GraphValueT],
    executable: ExecutableTask[GraphValueT],
    outcome: NodeReturn[GraphValueT],
) -> TaskResult[GraphValueT]:
    if type(outcome) not in (
        _GraphValues,
        _GraphSuccessOutcome,
        _GraphFailureOutcome,
        _GraphInterruptOutcome,
    ):
        raise NodeExecutionContractError("graph node returned an unsupported outcome")
    if isinstance(outcome, _GraphValues):
        output = outcome
        route = None
    elif isinstance(outcome, _GraphSuccessOutcome):
        output = outcome.output
        route = outcome.route
    elif isinstance(outcome, _GraphFailureOutcome):
        return TaskFailure(executable.task, outcome.failure)
    else:
        return TaskInterrupt(executable.task, outcome.request_payload)
    routing = ContinueGraphRouting() if route is None else SelectGraphRoute(GraphRouteId(route))
    validate_routing_contribution(graph, executable.task.node_id, routing)
    descriptor = graph.transition.publications[executable.task.node_id]
    declarations = tuple((item.name, item.descriptor) for item in descriptor.declarations.entries)
    return TaskSuccess(
        executable.task,
        _make_node_output_frame(output, declarations),
        route,
    )


async def _execute_task(
    graph: CompiledGraph[GraphValueT], executable: ExecutableTask[GraphValueT]
) -> TaskResult[GraphValueT]:
    definition = graph.nodes[executable.task.node_id]
    if isinstance(definition, NestedGraphNodeDefinition):
        raise NodeExecutionContractError("nested task must be projected to a precomputed terminal outcome")
    outcome = await definition.operation(_public_node_input(executable.effective_input))
    return _project_outcome(graph, executable, outcome)


async def _capture(
    graph: CompiledGraph[GraphValueT], executable: ExecutableTask[GraphValueT]
) -> TaskResult[GraphValueT] | TaskRaised:
    try:
        return await _execute_task(graph, executable)
    except asyncio.CancelledError as error:
        if error.args and error.args[0] is _SCHEDULER_CLOSE_CANCEL:
            raise
        return TaskRaised(executable.task, error)
    except Exception as error:
        return TaskRaised(executable.task, error)


class TaskScheduler(Generic[GraphValueT]):
    """Submit ordinary node tasks and yield one completion at a time."""

    __slots__ = ("_events", "_graph", "_live")

    def __init__(self, graph: CompiledGraph[GraphValueT]) -> None:
        self._graph = graph
        self._live: dict[
            TaskId,
            tuple[
                ExecutableTask[GraphValueT],
                asyncio.Task[TaskResult[GraphValueT] | TaskRaised],
            ],
        ] = {}
        self._events: list[TaskResult[GraphValueT] | TaskRaised] = []

    @property
    def live_count(self) -> int:
        return len(self._live)

    def drain_pending_events(
        self,
    ) -> tuple[tuple[TaskRaised, ...], tuple[TaskResult[GraphValueT], ...]]:
        """Split buffered events once while preserving each canonical order."""

        errors = tuple(event for event in self._events if isinstance(event, TaskRaised))
        completions = tuple(event for event in self._events if not isinstance(event, TaskRaised))
        self._events.clear()
        return errors, completions

    def submit(self, executables: tuple[ExecutableTask[GraphValueT], ...]) -> None:
        task_ids = tuple(executable.task.task_id for executable in executables)
        existing = set(self._live)
        existing.update(event.task.task_id for event in self._events)
        if len(task_ids) != len(set(task_ids)) or existing.intersection(task_ids):
            raise NodeExecutionContractError("a graph task was submitted more than once")
        for executable in executables:
            task_id = executable.task.task_id
            handle = asyncio.create_task(_capture(self._graph, executable), name=f"mote-graph:{task_id}")
            self._live[task_id] = (executable, handle)

    async def next_completion(self) -> TaskResult[GraphValueT] | TaskRaised:
        if self._events:
            return self._events.pop(0)
        if not self._live:
            raise NodeExecutionContractError("there are no live graph tasks")
        by_handle = {handle: executable for executable, handle in self._live.values()}
        done, _pending = await asyncio.wait(
            tuple(by_handle),
            return_when=asyncio.FIRST_COMPLETED,
        )
        events: list[tuple[tuple[int, str, TaskId], TaskResult[GraphValueT] | TaskRaised]] = []
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
            cancel_scheduler_task(handle)
        if handles:
            await asyncio.gather(*handles, return_exceptions=True)
        self._live.clear()
        self._events.clear()


__all__ = ["TaskRaised", "TaskScheduler"]

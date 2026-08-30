"""The single dynamic async task pool used by graph execution."""

import asyncio
from collections import deque
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
    return TaskSuccess(
        executable.task,
        _make_node_output_frame(output, graph.transition.publications[executable.task.node_id].declarations),
        route,
    )


async def _execute_task(
    graph: CompiledGraph[GraphValueT], executable: ExecutableTask[GraphValueT]
) -> TaskResult[GraphValueT] | TaskRaised:
    try:
        definition = graph.nodes[executable.task.node_id]
        if isinstance(definition, NestedGraphNodeDefinition):
            raise NodeExecutionContractError("nested task must be projected to a precomputed terminal outcome")
        outcome = await definition.operation(_public_node_input(executable.effective_input))
        return _project_outcome(graph, executable, outcome)
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
        self._live: dict[TaskId, asyncio.Task[TaskResult[GraphValueT] | TaskRaised]] = {}
        self._events: deque[TaskResult[GraphValueT] | TaskRaised] = deque()

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
        submitted = {executable.task.task_id for executable in executables}
        existing = set(self._live)
        existing.update(event.task.task_id for event in self._events)
        if len(submitted) != len(executables) or not existing.isdisjoint(submitted):
            raise NodeExecutionContractError("a graph task was submitted more than once")
        for executable in executables:
            task_id = executable.task.task_id
            handle = asyncio.create_task(_execute_task(self._graph, executable), name=f"mote-graph:{task_id}")
            self._live[task_id] = handle

    async def next_completion(self) -> TaskResult[GraphValueT] | TaskRaised:
        if self._events:
            return self._events.popleft()
        if not self._live:
            raise NodeExecutionContractError("there are no live graph tasks")
        done, _pending = await asyncio.wait(
            tuple(self._live.values()),
            return_when=asyncio.FIRST_COMPLETED,
        )
        events: list[TaskResult[GraphValueT] | TaskRaised] = []
        for handle in done:
            event = handle.result()
            self._live.pop(event.task.task_id)
            events.append(event)
        events.sort(key=lambda event: event.task.sort_key)
        self._events.extend(events[1:])
        return events[0]

    async def aclose(self) -> None:
        handles = tuple(self._live.values())
        for handle in handles:
            handle.cancel(_SCHEDULER_CLOSE_CANCEL)
        if handles:
            await asyncio.gather(*handles, return_exceptions=True)
        self._live.clear()
        self._events.clear()


__all__ = ["TaskRaised", "TaskScheduler"]

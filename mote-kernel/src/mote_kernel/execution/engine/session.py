"""State-acknowledged, one-completion-at-a-time graph execution sessions."""

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from types import TracebackType
from typing import Generic, Protocol, TypeVar

from mote_kernel.execution.cancellation import wait_for_owner_task
from mote_kernel.execution.claim import ConsumedExecutionClaim
from mote_kernel.execution.engine.admission import select_executable_tasks
from mote_kernel.execution.engine.frontier import FrontierPreparation
from mote_kernel.execution.engine.scheduler import TaskRaised, TaskScheduler
from mote_kernel.execution.engine.settlement import settle_result
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.task import ExecutableTask, TaskId
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.result import ExecutedGraphNode, TaskResult
from mote_kernel.state.graph_state import (
    GraphNodeId,
    GraphRunState,
    SettleGraphNode,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")
GraphValueT_co = TypeVar("GraphValueT_co")


class _SessionDisposition(Enum):
    OPEN = auto()
    ERROR_DRAINING = auto()
    QUIESCENT = auto()
    CLOSED = auto()


@dataclass(frozen=True, slots=True)
class _QueuedCompletion(Generic[GraphValueT]):
    result: TaskResult[GraphValueT]
    refill_ordinary_slots: bool = False


class GraphExecutionSession(Protocol[GraphValueT_co]):
    """Owner-internal single-consumer interface issued only by ``GraphExecutor``."""

    async def __aenter__(self) -> "GraphExecutionSession[GraphValueT_co]": ...

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None: ...

    async def next(self, state: GraphRunState) -> ExecutedGraphNode[GraphValueT_co]: ...

    async def aclose(self) -> None: ...


class _GraphExecutionSession(Generic[GraphValueT]):
    """Own live task handles while the caller owns state commits."""

    __slots__ = (
        "_awaiting_ack",
        "_close_lock",
        "_disposition",
        "_error",
        "_executables",
        "_graph",
        "_limits",
        "_next_in_progress",
        "_node_origin_cancellation",
        "_queued_results",
        "_scheduler",
        "_started",
        "_state",
    )

    def __init__(
        self,
        graph: CompiledGraph[GraphValueT],
        state: GraphRunState,
        preparation: FrontierPreparation[GraphValueT],
    ) -> None:
        self._graph = graph
        self._limits = preparation.request.limits
        self._executables = preparation.executables
        self._state = state
        self._queued_results = deque(_QueuedCompletion(result) for result in preparation.nested_results)
        self._started: set[GraphNodeId] = set()
        self._scheduler = TaskScheduler(graph)
        self._awaiting_ack: SettleGraphNode | None = None
        self._next_in_progress = False
        self._close_lock = asyncio.Lock()
        self._disposition = _SessionDisposition.OPEN
        self._error: TaskRaised | None = None
        self._node_origin_cancellation: asyncio.CancelledError | None = None

    async def __aenter__(self) -> GraphExecutionSession[GraphValueT]:
        return self

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _require_open(self) -> None:
        if self._disposition is _SessionDisposition.CLOSED:
            raise ResultCollectionError("execution session is closed")
        if self._disposition is _SessionDisposition.QUIESCENT:
            raise ResultCollectionError("execution session is quiescent")

    def _acknowledge(self, state: GraphRunState) -> None:
        command = self._awaiting_ack
        if command is None:
            require_snapshot_matches_graph(self._graph, state)
            if state != self._state:
                raise ResultCollectionError("first session state must be the reducer-applied claim successor")
            return
        if state != reduce_graph_run(self._state, command):
            raise ResultCollectionError("session state acknowledgement is not the exact reducer successor")
        self._state = state
        self._awaiting_ack = None

    def _select_ordinary(self) -> tuple[ExecutableTask[GraphValueT], ...]:
        if self._disposition is not _SessionDisposition.OPEN:
            return ()
        pending = frozenset(pending_node_ids(self._state.frontier))
        executables = tuple(executable for executable in self._executables if executable.task.node_id in pending)
        tasks = tuple(executable.task for executable in executables)
        selected = select_executable_tasks(
            self._graph,
            tasks,
            self._state.resources,
            self._limits,
            active_count=self._scheduler.live_count,
            started_node_ids=self._started,
        )
        by_task_id: dict[TaskId, ExecutableTask[GraphValueT]] = {
            executable.task.task_id: executable for executable in executables
        }
        return tuple(by_task_id[task.task_id] for task in selected)

    def _record_error(self, raised: TaskRaised) -> None:
        if self._error is None or raised.task.sort_key < self._error.task.sort_key:
            self._error = raised
        first = self._error.error
        self._node_origin_cancellation = first if isinstance(first, asyncio.CancelledError) else None
        self._disposition = _SessionDisposition.ERROR_DRAINING

    def consume_node_origin(self, error: asyncio.CancelledError) -> bool:
        if self._node_origin_cancellation is not error:
            return False
        self._node_origin_cancellation = None
        return True

    def _drain_scheduler_events(self) -> None:
        errors, completions = self._scheduler.drain_pending_events()
        for raised in errors:
            self._record_error(raised)
        self._queued_results.extend(_QueuedCompletion(result, True) for result in completions)

    def _schedule_ordinary(self) -> bool:
        selected = self._select_ordinary()
        self._scheduler.submit(selected)
        self._started.update(task.task.node_id for task in selected)
        return bool(selected)

    def _project(self, result: TaskResult[GraphValueT]) -> ExecutedGraphNode[GraphValueT]:
        command = settle_result(self._graph, self._state, result)
        self._awaiting_ack = command
        return ExecutedGraphNode(result, command)

    async def next(self, state: GraphRunState) -> ExecutedGraphNode[GraphValueT]:
        """Acknowledge the previous command and yield exactly one new completion."""

        self._require_open()
        if self._next_in_progress:
            raise ResultCollectionError("execution session already has an in-progress next call")
        self._next_in_progress = True
        try:
            self._acknowledge(state)
            while True:
                self._drain_scheduler_events()
                if self._node_origin_cancellation is not None:
                    cancellation = self._node_origin_cancellation
                    await self.aclose()
                    raise cancellation
                if self._queued_results:
                    queued = self._queued_results.popleft()
                    projected = self._project(queued.result)
                    if queued.refill_ordinary_slots and self._schedule_ordinary():
                        await asyncio.sleep(0)
                        self._require_open()
                    return projected

                self._schedule_ordinary()
                if self._scheduler.live_count == 0:
                    if self._error is not None:
                        self._disposition = _SessionDisposition.QUIESCENT
                        raise self._error.error
                    if not pending_node_ids(self._state.frontier):
                        self._disposition = _SessionDisposition.QUIESCENT
                        raise StopAsyncIteration
                    raise ResultCollectionError("no executable pending node can be scheduled")

                event = await self._scheduler.next_completion()
                if isinstance(event, TaskRaised):
                    self._record_error(event)
                    continue
                return self._project(event)
        except asyncio.CancelledError:
            await wait_for_owner_task(asyncio.create_task(self.aclose()))
            raise
        finally:
            self._next_in_progress = False

    async def aclose(self) -> None:
        """Cancel and await all live tasks; repeated calls are harmless."""

        async with self._close_lock:
            if self._disposition is _SessionDisposition.CLOSED:
                return
            self._disposition = _SessionDisposition.QUIESCENT
            await self._scheduler.aclose()
            self._queued_results.clear()
            self._awaiting_ack = None
            self._disposition = _SessionDisposition.CLOSED


def consume_node_origin_cancellation(
    session: GraphExecutionSession[GraphValueT],
    error: asyncio.CancelledError,
) -> bool:
    if not isinstance(session, _GraphExecutionSession):
        raise ResultCollectionError("execution session was not issued by the graph executor")
    return session.consume_node_origin(error)


def issue_execution_session(
    graph: CompiledGraph[GraphValueT],
    claim: ConsumedExecutionClaim[GraphValueT],
) -> GraphExecutionSession[GraphValueT]:
    """Issue the sole concrete session authorized by a consumed claim receipt."""

    state, preparation = claim.issue()
    return _GraphExecutionSession(graph, state, preparation)


__all__ = ["GraphExecutionSession"]

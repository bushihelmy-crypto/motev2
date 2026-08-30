"""State-acknowledged, one-completion-at-a-time graph execution sessions."""

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from types import TracebackType
from typing import Generic, Protocol, TypeVar, runtime_checkable

from mote_kernel.execution.cancellation import wait_for_owner_task
from mote_kernel.execution.claim import ConsumedExecutionClaim, ExecutionClaimSnapshot
from mote_kernel.execution.engine.admission import select_executable_tasks
from mote_kernel.execution.engine.frontier import FrontierPreparation
from mote_kernel.execution.engine.scheduler import TaskRaised, TaskScheduler
from mote_kernel.execution.engine.settlement import settle_result
from mote_kernel.execution.engine.snapshot_guard import GraphDefinitionKey, require_snapshot_matches_graph
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask, TaskId
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.result import ExecutedGraphNode, TaskResult
from mote_kernel.state.graph_state import (
    FailedGraphNode,
    FailedGraphNodeOutcome,
    GraphNodeId,
    GraphNodeInterrupt,
    GraphNodeSettlement,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    PendingGraphNode,
    SettleGraphNode,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    frontier_node,
    pending_node_ids,
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


@runtime_checkable
class GraphExecutionSession(Protocol[GraphValueT_co]):
    """Public single-consumer interface issued only by ``GraphExecutor``."""

    @property
    def quiescent(self) -> bool: ...

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
        "_claim_snapshot",
        "_close_lock",
        "_disposition",
        "_errors",
        "_graph",
        "_limits",
        "_next_in_progress",
        "_node_origin_cancellation",
        "_parent_nodes",
        "_preparation",
        "_queued_results",
        "_scheduler",
        "_started",
        "_state",
    )

    def __init__(
        self,
        graph: CompiledGraph[GraphValueT],
        state: GraphRunState,
        claim_snapshot: ExecutionClaimSnapshot,
        preparation: FrontierPreparation[GraphValueT],
        parent_nodes: frozenset[tuple[GraphDefinitionKey, GraphNodeId]] | None = None,
    ) -> None:
        self._graph = graph
        self._limits = preparation.request.limits
        self._claim_snapshot = claim_snapshot
        self._state = state
        self._parent_nodes = parent_nodes
        self._preparation = preparation
        self._queued_results: deque[_QueuedCompletion[GraphValueT]] = deque()
        self._started: set[GraphNodeId] = set()
        for result in preparation.nested_results:
            self._started.add(result.task.node_id)
            self._queued_results.append(_QueuedCompletion(result))
        self._scheduler = TaskScheduler(graph)
        self._awaiting_ack: SettleGraphNode | None = None
        self._next_in_progress = False
        self._close_lock = asyncio.Lock()
        self._disposition = _SessionDisposition.OPEN
        self._errors: list[tuple[GraphTask, BaseException]] = []
        self._node_origin_cancellation: asyncio.CancelledError | None = None

    @property
    def quiescent(self) -> bool:
        return (
            self._disposition in (_SessionDisposition.QUIESCENT, _SessionDisposition.CLOSED)
            and self._scheduler.live_count == 0
        )

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

    def _validate_initial_state(self, state: GraphRunState) -> None:
        require_snapshot_matches_graph(self._graph, state, self._parent_nodes)
        if state != self._state:
            raise ResultCollectionError("first session state must be the reducer-applied claim successor")

    @staticmethod
    def _expected_settlement(command: SettleGraphNode) -> GraphNodeSettlement:
        outcome = command.outcome
        if isinstance(outcome, SucceededGraphNodeOutcome):
            return SucceededGraphNode(outcome.routing)
        if isinstance(outcome, FailedGraphNodeOutcome):
            return FailedGraphNode(outcome.failure)
        return InterruptedGraphNode(GraphNodeInterrupt(outcome.identity, outcome.request_payload))

    def _acknowledge(self, state: GraphRunState) -> None:
        command = self._awaiting_ack
        if command is None:
            self._validate_initial_state(state)
            return
        require_snapshot_matches_graph(self._graph, state, self._parent_nodes)
        previous = self._state
        if (
            state.revision != previous.revision + 1
            or state.run_id != previous.run_id
            or state.definition_id != previous.definition_id
            or state.definition_version != previous.definition_version
            or state.superstep != previous.superstep
            or state.execution_sequence != previous.execution_sequence
            or state.status is not GraphRunStatus.RUNNING
            or state.join_progress != previous.join_progress
            or state.parent != previous.parent
            or state.resume_input_codec != previous.resume_input_codec
            or state.abort != previous.abort
            or tuple(node.node_id for node in state.frontier.nodes)
            != tuple(node.node_id for node in previous.frontier.nodes)
        ):
            raise ResultCollectionError("session state acknowledgement is not a single successor revision")
        for before, after in zip(previous.frontier.nodes, state.frontier.nodes, strict=True):
            if before.node_id != command.outcome.node_id and before.settlement != after.settlement:
                raise ResultCollectionError("acknowledged state changed an unrelated node settlement")
        target = frontier_node(state.frontier, command.outcome.node_id)
        if target is None or isinstance(target.settlement, PendingGraphNode):
            raise ResultCollectionError("acknowledged state did not settle the yielded node")
        expected = self._expected_settlement(command)
        if target.settlement != expected:
            raise ResultCollectionError("acknowledged node settlement does not match the yielded outcome")
        if state.execution is not None and state.execution.token != self._claim_snapshot.token:
            raise ResultCollectionError("acknowledged state changed the active execution token")
        if pending_node_ids(state.frontier) and state.execution is None:
            raise ResultCollectionError("an acknowledged partial frontier must retain its execution token")
        # ``require_snapshot_matches_graph`` has already validated the authoritative
        # complete-frontier quiescence invariant before this successor proof.
        self._state = state
        self._awaiting_ack = None

    def _select_ordinary(self) -> tuple[ExecutableTask[GraphValueT], ...]:
        if self._disposition is not _SessionDisposition.OPEN:
            return ()
        pending = frozenset(pending_node_ids(self._state.frontier))
        executables = tuple(
            executable for executable in self._preparation.executables if executable.task.node_id in pending
        )
        tasks = tuple(executable.task for executable in executables)
        selected = select_executable_tasks(
            self._graph,
            tasks,
            self._state.resources,
            self._limits,
            active_count=self._scheduler.live_count,
            started_node_ids=frozenset(self._started),
        )
        by_task_id: dict[TaskId, ExecutableTask[GraphValueT]] = {
            executable.task.task_id: executable for executable in executables
        }
        return tuple(by_task_id[task.task_id] for task in selected)

    def _record_error(self, task: GraphTask, error: BaseException) -> None:
        self._errors.append((task, error))
        self._errors.sort(key=lambda item: item[0].sort_key)
        first = self._errors[0][1]
        self._node_origin_cancellation = first if isinstance(first, asyncio.CancelledError) else None
        self._disposition = _SessionDisposition.ERROR_DRAINING

    def consume_node_origin(self, error: asyncio.CancelledError) -> bool:
        if self._node_origin_cancellation is not error:
            return False
        self._node_origin_cancellation = None
        return True

    async def _next_event(self) -> TaskResult[GraphValueT] | TaskRaised:
        return await self._scheduler.next_completion()

    def _drain_scheduler_events(self) -> None:
        errors, completions = self._scheduler.drain_pending_events()
        for raised in errors:
            self._record_error(raised.task, raised.error)
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

    async def _close_after_cancellation(self) -> None:
        """Finish close even if the cancelled caller receives further cancellation requests."""

        close_task = asyncio.create_task(self.aclose())
        await wait_for_owner_task(close_task)

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
                    if self._errors:
                        self._disposition = _SessionDisposition.QUIESCENT
                        raise self._errors[0][1]
                    if not pending_node_ids(self._state.frontier):
                        self._disposition = _SessionDisposition.QUIESCENT
                        raise StopAsyncIteration
                    raise ResultCollectionError("no executable pending node can be scheduled")

                event = await self._next_event()
                if isinstance(event, TaskRaised):
                    self._record_error(event.task, event.error)
                    continue
                return self._project(event)
        except asyncio.CancelledError:
            await self._close_after_cancellation()
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
    parent_nodes: frozenset[tuple[GraphDefinitionKey, GraphNodeId]] | None = None,
) -> GraphExecutionSession[GraphValueT]:
    """Issue the sole concrete session authorized by a consumed claim receipt."""

    snapshot, state, preparation = claim.issue()
    return _GraphExecutionSession(
        graph,
        state,
        snapshot,
        preparation,
        parent_nodes,
    )


__all__ = ["GraphExecutionSession"]

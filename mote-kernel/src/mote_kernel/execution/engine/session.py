"""State-acknowledged, one-completion-at-a-time graph execution sessions."""

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from types import TracebackType
from typing import Generic, Protocol, TypeVar, cast, runtime_checkable

from mote_kernel.execution.claim import ConsumedExecutionClaim, ExecutionClaimSnapshot
from mote_kernel.execution.engine.frontier import FrontierPreparation, prepare_frontier
from mote_kernel.execution.engine.resume_input import effective_node_input
from mote_kernel.execution.engine.scheduler import TaskRaised, TaskScheduler
from mote_kernel.execution.engine.settlement import settle_result
from mote_kernel.execution.engine.snapshot_guard import GraphDefinitionKey, require_snapshot_matches_graph
from mote_kernel.execution.engine.task import ExecutableTask, GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import CompiledGraph
from mote_kernel.execution.request import StepRequest
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

InputT = TypeVar("InputT")
InputT_co = TypeVar("InputT_co", covariant=True)
OutputT = TypeVar("OutputT")


class _SessionDisposition(Enum):
    OPEN = auto()
    ERROR_DRAINING = auto()
    QUIESCENT = auto()
    CLOSED = auto()


@dataclass(frozen=True, slots=True)
class _QueuedCompletion(Generic[OutputT]):
    result: TaskResult[OutputT]


@runtime_checkable
class GraphExecutionSession(Protocol[InputT_co, OutputT]):
    """Public single-consumer interface issued only by ``GraphExecutor``."""

    @property
    def quiescent(self) -> bool: ...

    async def __aenter__(self) -> "GraphExecutionSession[InputT_co, OutputT]": ...

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None: ...

    async def next(self, state: GraphRunState) -> ExecutedGraphNode[OutputT]: ...

    async def aclose(self) -> None: ...


class _GraphExecutionSession(Generic[InputT, OutputT]):
    """Own live task handles while the caller owns state commits."""

    __slots__ = (
        "_awaiting_ack",
        "_claim_snapshot",
        "_close_lock",
        "_disposition",
        "_errors",
        "_graph",
        "_nested",
        "_next_in_progress",
        "_parent_nodes",
        "_preparation",
        "_request",
        "_scheduler",
        "_started",
        "_state",
    )

    def __init__(
        self,
        graph: CompiledGraph[InputT, OutputT],
        request: StepRequest[InputT, OutputT],
        claim_snapshot: ExecutionClaimSnapshot,
        parent_nodes: frozenset[tuple[GraphDefinitionKey, GraphNodeId]] | None = None,
    ) -> None:
        self._graph = graph
        self._request = request
        self._claim_snapshot = claim_snapshot
        self._state = request.state
        self._parent_nodes = parent_nodes
        self._preparation: FrontierPreparation[InputT, OutputT] | None = None
        self._nested: deque[_QueuedCompletion[OutputT]] = deque()
        self._started: set[GraphNodeId] = set()
        self._scheduler = TaskScheduler(graph)
        self._awaiting_ack: SettleGraphNode | None = None
        self._next_in_progress = False
        self._close_lock = asyncio.Lock()
        self._disposition = _SessionDisposition.OPEN
        self._errors: list[tuple[GraphTask, Exception]] = []

    @property
    def quiescent(self) -> bool:
        return (
            self._disposition in (_SessionDisposition.QUIESCENT, _SessionDisposition.CLOSED)
            and self._scheduler.live_count == 0
        )

    async def __aenter__(self) -> GraphExecutionSession[InputT, OutputT]:
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

    def _ensure_preparation(self) -> FrontierPreparation[InputT, OutputT]:
        if self._preparation is None:
            self._preparation = prepare_frontier(self._graph, self._request)
            for result in self._preparation.nested_results:
                self._started.add(result.task.node_id)
                self._nested.append(_QueuedCompletion(result))
        return self._preparation

    def _select_ordinary(self) -> tuple[ExecutableTask[InputT], ...]:
        preparation = self._ensure_preparation()
        available_slots = self._request.limits.max_parallel_tasks - self._scheduler.live_count
        if available_slots <= 0 or self._disposition is not _SessionDisposition.OPEN:
            return ()
        pending = frozenset(pending_node_ids(self._state.frontier))
        selected: list[ExecutableTask[InputT]] = []
        acquisitions = {
            item.node_id: item for item in (self._state.resources.acquisitions if self._state.resources else ())
        }
        for task, definition in sorted(preparation.executable_definitions, key=lambda item: item[0].sort_key):
            if len(selected) >= available_slots or task.node_id in self._started or task.node_id not in pending:
                continue
            if definition.resources:
                acquisition = acquisitions.get(task.node_id)
                if acquisition is None or not acquisition.admitted or acquisition.required != definition.resources:
                    continue
            effective = effective_node_input(self._graph, self._state, task.node_id, self._request.node_input)
            selected.append(ExecutableTask(task, effective))
        return tuple(selected)

    def _record_error(self, task: GraphTask, error: Exception) -> None:
        self._errors.append((task, error))
        self._errors.sort(key=lambda item: item[0].sort_key)
        self._disposition = _SessionDisposition.ERROR_DRAINING

    async def _next_event(self) -> TaskResult[OutputT] | TaskRaised:
        return await self._scheduler.next_completion()

    def _drain_pending_errors(self) -> None:
        for raised in self._scheduler.take_pending_errors():
            self._record_error(raised.task, raised.error)

    def _schedule_ordinary(self) -> bool:
        selected = self._select_ordinary()
        self._scheduler.submit(selected)
        self._started.update(task.task.node_id for task in selected)
        return bool(selected)

    def _project(self, result: TaskResult[OutputT]) -> ExecutedGraphNode[OutputT]:
        command = settle_result(self._graph, self._state, result)
        self._awaiting_ack = command
        return ExecutedGraphNode(result, command)

    async def _close_after_cancellation(self) -> None:
        """Finish close even if the cancelled caller receives further cancellation requests."""

        close_task = asyncio.create_task(self.aclose())
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                continue
        close_task.result()

    async def next(self, state: GraphRunState) -> ExecutedGraphNode[OutputT]:
        """Acknowledge the previous command and yield exactly one new completion."""

        self._require_open()
        if self._next_in_progress:
            raise ResultCollectionError("execution session already has an in-progress next call")
        self._next_in_progress = True
        try:
            self._acknowledge(state)
            self._ensure_preparation()
            while True:
                if self._nested:
                    queued = self._nested.popleft()
                    try:
                        return self._project(queued.result)
                    except Exception as error:
                        self._record_error(queued.result.task, error)
                        continue

                self._drain_pending_errors()
                if self._scheduler.has_pending_events:
                    event = cast(TaskResult[OutputT], await self._next_event())
                    try:
                        projected = self._project(event)
                    except Exception as error:
                        self._record_error(event.task, error)
                        continue
                    if self._schedule_ordinary():
                        await asyncio.sleep(0)
                        self._require_open()
                    return projected

                self._schedule_ordinary()
                if self._scheduler.live_count == 0 and not self._scheduler.has_pending_events:
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
                try:
                    return self._project(event)
                except Exception as error:
                    # A malformed typed result is an ordinary execution error; drain siblings first.
                    self._record_error(event.task, error)
                    continue
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
            self._nested.clear()
            self._awaiting_ack = None
            self._disposition = _SessionDisposition.CLOSED


def issue_execution_session(
    graph: CompiledGraph[InputT, OutputT],
    request: StepRequest[InputT, OutputT],
    claim: ConsumedExecutionClaim,
    parent_nodes: frozenset[tuple[GraphDefinitionKey, GraphNodeId]] | None = None,
) -> GraphExecutionSession[InputT, OutputT]:
    """Issue the sole concrete session authorized by a consumed claim receipt."""

    return _GraphExecutionSession(
        graph,
        request,
        claim.issue(request.state, request.request_attempt_id),
        parent_nodes,
    )


__all__ = ["GraphExecutionSession"]

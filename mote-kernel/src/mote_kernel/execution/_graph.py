"""Private implementation of the single public graph facade."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar, Generic, Protocol, Self, TypeAlias, TypeVar, cast
from uuid import uuid4

from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.errors import (
    ExecutionError,
    ExecutionLimitError,
    GraphValidationError,
    SnapshotMismatchError,
)
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph import (
    END,
    START,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    JoinEdge,
    NodeDefinition,
    NodeFailure,
    NodeInterrupt,
    NodeOutcome,
    NodeSuccess,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.graph.edge import Edge
from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeNodeRequest,
    ResumeRequest,
    SkipFailedNodeRequest,
    StepRequest,
    UseRequestInput,
)
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.execution.result import (
    ExecutableFrontier,
    ReadyToResolve,
    TaskFailure,
    TaskInterrupt,
    TaskResult,
    TaskSuccess,
    WaitingForChildren,
)
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    FailedGraphNode,
    FenceGraphExecution,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionLease,
    GraphFailure,
    GraphFrontierStatus,
    GraphInterruptId,
    GraphInterruptPayload,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRoutingContribution,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    InterruptedGraphNode,
    SelectGraphRoute,
    frontier_status,
    graph_interrupt_id,
    reduce_graph_run,
)

InputT = TypeVar("InputT")
InputT_contra = TypeVar("InputT_contra", contravariant=True)
OutputT = TypeVar("OutputT")
OutputT_co = TypeVar("OutputT_co", covariant=True)
ValueT = TypeVar("ValueT")


class _NodeCallable(Protocol[InputT_contra, OutputT_co]):
    async def __call__(self, node_input: InputT_contra, /) -> NodeOutcome[OutputT_co] | OutputT_co: ...


@dataclass(frozen=True, slots=True)
class _NodeAdapter(Generic[InputT, OutputT]):
    operation: _NodeCallable[InputT, OutputT]

    async def __call__(self, node_input: InputT, /) -> NodeOutcome[OutputT]:
        outcome = await self.operation(node_input)
        if isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            outcome, NodeSuccess | NodeFailure | NodeInterrupt
        ):
            return cast(NodeOutcome[OutputT], outcome)
        return NodeSuccess(outcome)


@dataclass(frozen=True, slots=True)
class _ResumeCodec(Generic[InputT]):
    encoder: Callable[[InputT], bytes]
    decoder: Callable[[bytes], InputT]

    def encode(self, value: InputT) -> bytes:
        return self.encoder(value)

    def decode(self, payload: bytes) -> InputT:
        return self.decoder(payload)


@dataclass(frozen=True, slots=True)
class _GraphTransition(Generic[OutputT]):
    """One reducer candidate offered for authoritative commit confirmation."""

    previous_state: GraphRunState | None
    command: GraphRunCommand
    next_state: GraphRunState
    result: TaskResult[OutputT] | None = None


_GraphCommit: TypeAlias = Callable[[_GraphTransition[OutputT]], Awaitable[GraphRunState]]


@dataclass(frozen=True, slots=True)
class _GraphNodeOutput(Generic[OutputT]):
    node_id: GraphNodeId
    output: OutputT


@dataclass(frozen=True, slots=True)
class _GraphFailureView:
    node_id: GraphNodeId
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class _GraphInterruptView:
    node_id: GraphNodeId
    interrupt_id: GraphInterruptId
    request_payload: GraphInterruptPayload


@dataclass(frozen=True, slots=True)
class _GraphRunResult(Generic[OutputT]):
    """Authoritative terminal/boundary state plus transient outputs from this invocation."""

    state: GraphRunState
    outputs: tuple[_GraphNodeOutput[OutputT], ...]

    @property
    def completed(self) -> bool:
        return self.state.status is GraphRunStatus.COMPLETED

    @property
    def aborted(self) -> bool:
        return self.state.status is GraphRunStatus.ABORTED

    @property
    def awaiting_resume(self) -> bool:
        return (
            self.state.status is GraphRunStatus.RUNNING
            and frontier_status(self.state.frontier) is GraphFrontierStatus.AWAITING_RESUME
        )

    @property
    def failures(self) -> tuple[_GraphFailureView, ...]:
        return tuple(
            _GraphFailureView(node.node_id, node.settlement.failure)
            for node in self.state.frontier.nodes
            if isinstance(node.settlement, FailedGraphNode)
        )

    @property
    def interrupts(self) -> tuple[_GraphInterruptView, ...]:
        views: list[_GraphInterruptView] = []
        for node in self.state.frontier.nodes:
            settlement = node.settlement
            if not isinstance(settlement, InterruptedGraphNode):
                continue
            identity = settlement.interrupt.identity
            views.append(
                _GraphInterruptView(
                    node.node_id,
                    graph_interrupt_id(
                        identity.run_id,
                        identity.superstep,
                        identity.node_id,
                        identity.execution_generation,
                    ),
                    settlement.interrupt.request_payload,
                )
            )
        return tuple(views)


async def _commit_transition(
    previous_state: GraphRunState | None,
    command: GraphRunCommand,
    result: TaskResult[OutputT] | None,
    commit: _GraphCommit[OutputT] | None,
) -> GraphRunState:
    candidate = reduce_graph_run(previous_state, command)
    if commit is None:
        return candidate
    confirmed = await commit(_GraphTransition(previous_state, command, candidate, result))
    if not isinstance(confirmed, GraphRunState) or confirmed != candidate:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise SnapshotMismatchError("commit must return the exact authoritative reducer successor")
    return confirmed


def _routing(route: str | None) -> GraphRoutingContribution:
    if route is None:
        return ContinueGraphRouting()
    return SelectGraphRoute(GraphRouteId(route))


def _canonical_resume_actions(
    actions: tuple[ResumeNodeRequest[InputT], ...],
) -> tuple[ResumeNodeRequest[InputT], ...]:
    if any(
        not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            action, ResumeFailedNodeRequest | ResumeInterruptedNodeRequest | SkipFailedNodeRequest
        )
        for action in actions
    ):
        return actions
    return tuple(sorted(actions, key=lambda action: action.node_id))


class Graph(Generic[InputT, OutputT]):
    """Build one immutable topology and drive it through the sole execution engine."""

    START: ClassVar[str] = START
    END: ClassVar[str] = END
    Outcome = NodeOutcome
    Result = _GraphRunResult
    State = GraphRunState
    Transition = _GraphTransition
    SuccessResult = TaskSuccess
    FailureResult = TaskFailure
    InterruptResult = TaskInterrupt
    Error = ExecutionError
    ValidationError = GraphValidationError
    SnapshotMismatchError = SnapshotMismatchError
    ExecutionLimitError = ExecutionLimitError

    __slots__ = (
        "_definition_id",
        "_edges",
        "_entries",
        "_executor",
        "_nodes",
        "_resources",
        "_resume_input",
        "_version",
    )

    def __init__(self, definition_id: str, *, version: int = 1) -> None:
        self._definition_id = GraphDefinitionId(definition_id)
        self._version = GraphDefinitionVersion(version)
        self._nodes: list[NodeDefinition[InputT, OutputT]] = []
        self._edges: list[Edge] = []
        self._entries: tuple[GraphNodeId, ...] = ()
        self._resources: list[ResourceDefinition] = []
        self._resume_input: ResumeInputBinding[InputT] | None = None
        self._executor: GraphExecutor[InputT, OutputT] | None = None

    def _require_mutable(self) -> None:
        if self._executor is not None:
            raise GraphValidationError("a graph topology is immutable after its first run")

    @staticmethod
    def _target(node_id: str) -> GraphNodeId:
        return END if node_id == Graph.END else GraphNodeId(node_id)

    def _register_resource_requirements(self, resources: tuple[str, ...]) -> tuple[ResourceId, ...]:
        resource_ids = tuple(ResourceId(resource_id) for resource_id in resources)
        registered = {definition.resource_id for definition in self._resources}
        for resource_id in resource_ids:
            if resource_id in registered:
                continue
            self._resources.append(ResourceDefinition(resource_id, len(self._resources)))
            registered.add(resource_id)
        return resource_ids

    def add_node(
        self,
        node_id: str,
        operation: _NodeCallable[InputT, OutputT],
        *,
        resources: tuple[str, ...] = (),
    ) -> Self:
        """Add one async node; plain return values are successful outcomes."""

        self._require_mutable()
        resource_ids = self._register_resource_requirements(resources)
        self._nodes.append(
            NodeDefinition(
                GraphNodeId(node_id),
                _NodeAdapter(operation),
                resource_ids,
            )
        )
        return self

    def add_edge(self, source: str, target: str) -> Self:
        self._require_mutable()
        if source == Graph.START:
            self._entries = (*self._entries, self._target(target))
            return self
        self._edges.append(DirectEdge(GraphNodeId(source), self._target(target)))
        return self

    def add_conditional_edge(self, source: str, route: str, target: str) -> Self:
        self._require_mutable()
        self._edges.append(ConditionalEdge(GraphNodeId(source), GraphRouteId(route), self._target(target)))
        return self

    def add_join(self, sources: tuple[str, ...], target: str) -> Self:
        self._require_mutable()
        self._edges.append(JoinEdge(tuple(GraphNodeId(source) for source in sources), self._target(target)))
        return self

    def set_resume_codec(
        self,
        codec_id: str,
        version: int,
        encoder: Callable[[InputT], bytes],
        decoder: Callable[[bytes], InputT],
    ) -> Self:
        """Bind the deterministic input codec required by override and interrupt resume."""

        self._require_mutable()
        codec = _ResumeCodec(encoder, decoder)
        self._resume_input = ResumeInputBinding(GraphResumeInputCodecId(codec_id), version, codec, codec)
        return self

    @staticmethod
    def success(output: ValueT, *, route: str | None = None) -> NodeSuccess[ValueT]:
        return NodeSuccess(output, _routing(route))

    @staticmethod
    def failure(reason: str) -> NodeFailure:
        return NodeFailure(GraphFailure(reason))

    @staticmethod
    def interrupt(request_payload: bytes) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(request_payload))

    def resume_failed(self, node_id: str) -> ResumeFailedNodeRequest[InputT]:
        return ResumeFailedNodeRequest(GraphNodeId(node_id), UseRequestInput())

    def resume_failed_with(self, node_id: str, node_input: InputT) -> ResumeFailedNodeRequest[InputT]:
        return ResumeFailedNodeRequest(GraphNodeId(node_id), OverrideNodeInput(node_input))

    def resume_interrupted(
        self,
        node_id: str,
        interrupt_id: str,
        node_input: InputT,
    ) -> ResumeInterruptedNodeRequest[InputT]:
        return ResumeInterruptedNodeRequest(
            GraphNodeId(node_id),
            GraphInterruptId(interrupt_id),
            OverrideNodeInput(node_input),
        )

    def skip_failed(self, node_id: str, reason: str, *, route: str | None = None) -> SkipFailedNodeRequest:
        return SkipFailedNodeRequest(GraphNodeId(node_id), GraphSkipReason(reason), _routing(route))

    def _executor_for_run(self) -> GraphExecutor[InputT, OutputT]:
        executor = self._executor
        if executor is None:
            definition = GraphDefinition(
                self._definition_id,
                self._version,
                tuple(self._nodes),
                tuple(self._edges),
                self._entries,
                tuple(self._resources),
                self._resume_input,
            )
            executor = GraphExecutor(compile_graph(definition))
            self._executor = executor
        return executor

    async def _execute_frontier(
        self,
        executor: GraphExecutor[InputT, OutputT],
        prepared: ExecutableFrontier,
        state: GraphRunState,
        node_input: InputT,
        request_attempt_id: ExecutionRequestAttemptId,
        limits: ExecutionLimits,
        commit: _GraphCommit[OutputT] | None,
        outputs: list[_GraphNodeOutput[OutputT]],
    ) -> GraphRunState:
        claimed = await _commit_transition(state, prepared.claim.command, None, commit)
        request = StepRequest(claimed, node_input, request_attempt_id, (), limits)
        try:
            session = await executor.execute(prepared.claim, request)
        except Exception:
            execution = cast(GraphExecutionLease, claimed.execution)
            await _commit_transition(
                claimed,
                FenceGraphExecution(claimed.revision, execution.token),
                None,
                commit,
            )
            raise
        return await self._consume_session(session, claimed, commit, outputs)

    async def _consume_session(
        self,
        session: GraphExecutionSession[InputT, OutputT],
        state: GraphRunState,
        commit: _GraphCommit[OutputT] | None,
        outputs: list[_GraphNodeOutput[OutputT]],
    ) -> GraphRunState:
        async with session:
            while True:
                try:
                    completed = await session.next(state)
                except StopAsyncIteration:
                    return state
                except Exception:
                    await session.aclose()
                    execution = cast(GraphExecutionLease, state.execution)
                    state = await _commit_transition(
                        state,
                        FenceGraphExecution(state.revision, execution.token),
                        None,
                        commit,
                    )
                    raise
                state = await _commit_transition(state, completed.command, completed.result, commit)
                if isinstance(completed.result, TaskSuccess):
                    outputs.append(_GraphNodeOutput(completed.result.task.node_id, completed.result.output))

    async def _drive(
        self,
        executor: GraphExecutor[InputT, OutputT],
        state: GraphRunState,
        node_input: InputT,
        limits: ExecutionLimits,
        commit: _GraphCommit[OutputT] | None,
        outputs: list[_GraphNodeOutput[OutputT]],
    ) -> GraphRunState:
        while True:
            request_attempt_id = ExecutionRequestAttemptId(str(uuid4()))
            request = StepRequest(state, node_input, request_attempt_id, (), limits)
            disposition = await executor.prepare(request)
            if isinstance(disposition, ReadyToResolve):
                state = await _commit_transition(state, disposition.command, None, commit)
                continue
            if isinstance(disposition, ExecutableFrontier):
                state = await self._execute_frontier(
                    executor,
                    disposition,
                    state,
                    node_input,
                    request_attempt_id,
                    limits,
                    commit,
                    outputs,
                )
                continue
            if isinstance(disposition, WaitingForChildren):
                raise GraphValidationError("the public Graph facade does not compose nested graph nodes")
            return state

    async def run(
        self,
        node_input: InputT,
        *,
        run_id: str | None = None,
        state: GraphRunState | None = None,
        resume: tuple[ResumeNodeRequest[InputT], ...] = (),
        commit: _GraphCommit[OutputT] | None = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> _GraphRunResult[OutputT]:
        """Start or continue one run, including selective resume, through one execution path.

        Passing an active state confirms that its previous execution attempt has
        stopped or been lost and may therefore be fenced and reclaimed. This does
        not provide multi-worker arbitration or exactly-once port side effects.
        """

        limits = ExecutionLimits(max_supersteps, max_parallel_tasks)
        executor = self._executor_for_run()
        outputs: list[_GraphNodeOutput[OutputT]] = []
        if state is None:
            if resume:
                raise SnapshotMismatchError("a new graph run cannot carry resume actions")
            effective_run_id = GraphRunId(str(uuid4()) if run_id is None else run_id)
            current = await _commit_transition(None, executor.start_command(effective_run_id), None, commit)
        else:
            executor.validate_state(state)
            if run_id is not None and state.run_id != GraphRunId(run_id):
                raise SnapshotMismatchError("run_id does not match the authoritative graph state")
            current = state
            execution = current.execution
            if execution is not None:
                current = await _commit_transition(
                    current,
                    FenceGraphExecution(current.revision, execution.token),
                    None,
                    commit,
                )
            if resume:
                command = executor.resume(ResumeRequest(current, _canonical_resume_actions(resume)))
                current = await _commit_transition(current, command, None, commit)
        current = await self._drive(
            executor,
            current,
            node_input,
            limits,
            commit,
            outputs,
        )
        return _GraphRunResult(current, tuple(outputs))


__all__ = ["Graph"]

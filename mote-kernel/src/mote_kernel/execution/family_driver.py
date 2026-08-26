"""Authoritative graph-family transition, driving, and result projection."""

from dataclasses import InitVar, dataclass, replace
from typing import Generic, Protocol, TypeVar, final
from uuid import uuid4

from mote_kernel.execution.engine.admission import (
    admit_child_graph_input,
    project_graph_outputs,
)
from mote_kernel.execution.engine.resume_input import (
    materialize_node_input,
)
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.errors import (
    SnapshotMismatchError,
)
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    _public_values,
)
from mote_kernel.execution.identity import (
    ExecutionRequestAttemptId,
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import (
    StepRequest,
)
from mote_kernel.execution.result import (
    AbortedChild,
    AbortedGraph,
    ActiveChild,
    AwaitingResume,
    CompletedChild,
    CompletedGraph,
    ExecutableFrontier,
    GraphAbortView,
    GraphBoundary,
    GraphCommitResult,
    GraphFailureView,
    GraphInterruptView,
    GraphResult,
    MissingChild,
    ReadyToResolve,
    StartMissingChildren,
    TaskResult,
    TaskSuccess,
    WaitingForChildren,
    _aborted_result,
    _awaiting_result,
    _commit_result,
    _completed_result,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ChildBoundaryAvailabilityCoordinate,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    GraphRunContext,
    PublicationAvailabilityCoordinate,
    _continuation,
)
from mote_kernel.state.graph_state import (
    FailedGraphNode,
    FenceGraphExecution,
    GraphExecutionToken,
    GraphFrontierStatus,
    GraphNodeId,
    GraphRunCommand,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    ParentGraphActivation,
    frontier_status,
    graph_interrupt_id,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")


class _TransitionSeal:
    __slots__ = ()


_TRANSITION_SEAL = _TransitionSeal()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class GraphTransition(Generic[GraphValueT]):
    """One reducer candidate offered to the caller's commit port."""

    scope: tuple[str, ...]
    previous_state: GraphRunState | None
    command: GraphRunCommand
    candidate_state: GraphRunState
    result: GraphCommitResult[GraphValueT] | None
    _seal: InitVar[_TransitionSeal]

    def __post_init__(self, _seal: _TransitionSeal) -> None:
        if _seal is not _TRANSITION_SEAL:
            raise SnapshotMismatchError("graph transitions can only be produced by the family driver")


class GraphCommit(Protocol[GraphValueT]):
    async def __call__(
        self,
        transition: GraphTransition[GraphValueT],
        /,
    ) -> GraphRunState: ...


async def commit_transition(
    scope_run: ScopeRunCoordinate,
    previous_state: GraphRunState | None,
    command: GraphRunCommand,
    result: TaskResult[GraphValueT] | None,
    commit: GraphCommit[GraphValueT] | None,
) -> GraphRunState:
    """Reduce, expose, and confirm one authoritative state transition."""

    candidate = reduce_graph_run(previous_state, command)
    admitted = _commit_result(result) if result is not None else None
    transition = GraphTransition(
        scope=tuple(scope_run.scope),
        previous_state=previous_state,
        command=command,
        candidate_state=candidate,
        result=admitted,
        _seal=_TRANSITION_SEAL,
    )
    if commit is None:
        return candidate
    confirmed = await commit(transition)
    if type(confirmed) is not GraphRunState or confirmed != candidate:
        raise SnapshotMismatchError("commit must return the exact authoritative reducer successor")
    return confirmed


def _request(
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
    limits: ExecutionLimits,
    projections: tuple[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild, ...],
) -> StepRequest[GraphValueT]:
    return StepRequest(
        state,
        scope_run,
        context.frames,
        ExecutionRequestAttemptId(str(uuid4())),
        projections,
        limits,
    )


def _ensure_child_boundary(
    graph: CompiledGraph[GraphValueT],
    coordinate: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
) -> None:
    state = context.state_at(coordinate)
    availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
        coordinate,
        graph.graph_output_descriptor.identity,
    )
    if context.frames.has_child_boundary(availability):
        return
    view = project_graph_outputs(graph, coordinate, state.superstep, context.frames)
    context.frames = context.frames.add_child_boundary(ConfirmedChildBoundary(availability, view))


def _child_projections(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
) -> tuple[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild, ...]:
    projections: list[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild] = []
    for node_id in pending_node_ids(state.frontier):
        if node_id not in graph.nested_graphs:
            continue
        parent = ParentGraphActivation(state.run_id, state.superstep, node_id)
        coordinate = child_scope_run_for_activation(scope_run, parent)
        binding = context.child_state(coordinate)
        if binding is None:
            projections.append(MissingChild(parent))
            continue
        child_state = binding.state
        child_graph = graph.nested_graphs[node_id]
        if child_state.status is GraphRunStatus.RUNNING:
            projections.append(ActiveChild(parent, child_state))
        elif child_state.status is GraphRunStatus.COMPLETED:
            _ensure_child_boundary(child_graph, coordinate, context)
            availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
                coordinate,
                child_graph.graph_output_descriptor.identity,
            )
            projections.append(CompletedChild(parent, child_state, context.frames.lookup(availability).frame))
        else:
            projections.append(AbortedChild(parent, child_state))
    return tuple(projections)


async def _consume_session(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    session: GraphExecutionSession[GraphValueT],
    execution_token: GraphExecutionToken,
    context: GraphRunContext[GraphValueT],
    commit: GraphCommit[GraphValueT] | None,
) -> None:
    state = context.state_at(scope_run)
    async with session:
        while True:
            try:
                completed = await session.next(state)
            except StopAsyncIteration:
                return
            except Exception:
                await session.aclose()
                fenced = await commit_transition(
                    scope_run,
                    state,
                    FenceGraphExecution(state.revision, execution_token),
                    None,
                    commit,
                )
                context.replace_state(scope_run, fenced)
                raise
            confirmed = await commit_transition(
                scope_run,
                state,
                completed.command,
                completed.result,
                commit,
            )
            context.replace_state(scope_run, confirmed)
            if isinstance(completed.result, TaskSuccess):
                activation = StableActivation(scope_run, state.superstep, completed.result.task.node_id)
                coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
                    activation,
                    graph.transition.publications[completed.result.task.node_id].identity,
                )
                context.frames = context.frames.add_publication(
                    ConfirmedPublication(
                        coordinate,
                        completed.result.output,
                        confirmed.revision,
                        ExecutionPublicationProvenance(completed.command.execution),
                    )
                )
            state = confirmed


async def _execute_frontier(
    graph: CompiledGraph[GraphValueT],
    executor: GraphExecutor[GraphValueT],
    scope_run: ScopeRunCoordinate,
    prepared: ExecutableFrontier,
    prepared_request: StepRequest[GraphValueT],
    context: GraphRunContext[GraphValueT],
    commit: GraphCommit[GraphValueT] | None,
) -> None:
    state = context.state_at(scope_run)
    claimed = await commit_transition(scope_run, state, prepared.claim.command, None, commit)
    context.replace_state(scope_run, claimed)
    request = replace(prepared_request, state=claimed)
    try:
        session = await executor.execute(prepared.claim, request)
    except Exception:
        fenced = await commit_transition(
            scope_run,
            claimed,
            FenceGraphExecution(claimed.revision, prepared.claim.snapshot.token),
            None,
            commit,
        )
        context.replace_state(scope_run, fenced)
        raise
    await _consume_session(
        graph,
        scope_run,
        session,
        prepared.claim.snapshot.token,
        context,
        commit,
    )


async def _start_missing_children(
    parent_graph: CompiledGraph[GraphValueT],
    parent_scope_run: ScopeRunCoordinate,
    action: StartMissingChildren[GraphValueT],
    context: GraphRunContext[GraphValueT],
    commit: GraphCommit[GraphValueT] | None,
) -> None:
    parent_state = context.state_at(parent_scope_run)
    for child in action.children:
        coordinate = child_scope_run_for_activation(parent_scope_run, child.parent)
        input_frame = materialize_node_input(
            parent_graph,
            parent_state,
            parent_scope_run,
            context.frames,
            child.parent.node_id,
        )
        child_input = admit_child_graph_input(child.graph, input_frame)
        confirmed = await commit_transition(coordinate, None, child.command, None, commit)
        activation = StableActivation(
            parent_scope_run,
            child.parent.superstep,
            child.parent.node_id,
        )
        context.replace_child(ChildStateBinding(coordinate, activation, confirmed))
        availability: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
            coordinate,
            child.graph.graph_input_descriptor.identity,
        )
        context.frames = context.frames.add_graph_input(AdmittedGraphInput(availability, child_input))


async def _advance_scope_quantum(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    context: GraphRunContext[GraphValueT],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> GraphBoundary | None:
    state = context.state_at(scope_run)
    projections = _child_projections(
        graph,
        state,
        scope_run,
        context,
    )
    request = _request(state, scope_run, context, limits, projections)
    disposition = await executors[graph.definition_scope].prepare(request)
    if isinstance(disposition, ReadyToResolve):
        confirmed = await commit_transition(
            scope_run,
            state,
            disposition.command,
            None,
            commit,
        )
        context.replace_state(scope_run, confirmed)
        return None
    if isinstance(disposition, ExecutableFrontier):
        await _execute_frontier(
            graph,
            executors[graph.definition_scope],
            scope_run,
            disposition,
            request,
            context,
            commit,
        )
        return None
    if isinstance(disposition, WaitingForChildren):
        awaiting = await _drive_children(
            graph,
            scope_run,
            disposition,
            context,
            executors,
            limits,
            commit,
        )
        if awaiting:
            return AwaitingResume((), ())
        return None
    return disposition


async def _drive_children(
    parent_graph: CompiledGraph[GraphValueT],
    parent_scope_run: ScopeRunCoordinate,
    waiting: WaitingForChildren[GraphValueT],
    context: GraphRunContext[GraphValueT],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> bool:
    if isinstance(waiting.action, StartMissingChildren):
        await _start_missing_children(
            parent_graph,
            parent_scope_run,
            waiting.action,
            context,
            commit,
        )
    while True:
        parent_state = context.state_at(parent_scope_run)
        projections = _child_projections(
            parent_graph,
            parent_state,
            parent_scope_run,
            context,
        )
        active = tuple(projection for projection in projections if isinstance(projection, ActiveChild))
        if not active:
            return False
        runnable = tuple(
            projection
            for projection in active
            if frontier_status(projection.child_state.frontier) is not GraphFrontierStatus.AWAITING_RESUME
        )
        if not runnable:
            return True
        for projection in runnable:
            coordinate = child_scope_run_for_activation(parent_scope_run, projection.parent)
            child_graph = parent_graph.nested_graphs[projection.parent.node_id]
            await _advance_scope_quantum(
                child_graph,
                coordinate,
                context,
                executors,
                limits,
                commit,
            )


async def drive_root(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
    executors: dict[tuple[GraphNodeId, ...], GraphExecutor[GraphValueT]],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> GraphBoundary:
    scope_run = root_scope_run(context.root_state.run_id)
    while True:
        disposition = await _advance_scope_quantum(
            graph,
            scope_run,
            context,
            executors,
            limits,
            commit,
        )
        if disposition is not None:
            return disposition


def _scoped_states(
    context: GraphRunContext[GraphValueT],
) -> tuple[tuple[tuple[str, ...], GraphRunState], ...]:
    return (
        ((), context.root_state),
        *((tuple(binding.coordinate.scope), binding.state) for binding in context.child_states),
    )


def _project_result_views(
    context: GraphRunContext[GraphValueT],
) -> tuple[tuple[GraphFailureView, ...], tuple[GraphInterruptView, ...]]:
    failures: list[GraphFailureView] = []
    interrupts: list[GraphInterruptView] = []
    for scope, state in _scoped_states(context):
        for node in state.frontier.nodes:
            settlement = node.settlement
            if isinstance(settlement, FailedGraphNode):
                failures.append(GraphFailureView(scope, node.node_id, str(settlement.failure)))
            elif isinstance(settlement, InterruptedGraphNode):
                identity = settlement.interrupt.identity
                interrupts.append(
                    GraphInterruptView(
                        scope,
                        node.node_id,
                        graph_interrupt_id(
                            identity.run_id,
                            identity.superstep,
                            identity.node_id,
                            identity.execution_generation,
                        ),
                        bytes(settlement.interrupt.request_payload),
                    )
                )
    return tuple(failures), tuple(interrupts)


def project_graph_result(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
    disposition: GraphBoundary,
) -> GraphResult[GraphValueT]:
    state = context.root_state
    continuation = _continuation(context)
    if isinstance(disposition, CompletedGraph):
        view = project_graph_outputs(
            graph,
            root_scope_run(state.run_id),
            state.superstep,
            context.frames,
        )
        return _completed_result(state, continuation, _public_values(view))
    if isinstance(disposition, AbortedGraph):
        if state.abort is None:
            raise SnapshotMismatchError("aborted root state is missing its canonical abort")
        return _aborted_result(state, continuation, GraphAbortView((), state.abort.reason))
    failures, interrupts = _project_result_views(context)
    return _awaiting_result(state, continuation, failures, interrupts)


__all__: list[str] = []

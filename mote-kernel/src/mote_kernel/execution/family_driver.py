"""Owner-local graph-run transition, driving, and result projection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import InitVar, dataclass, replace
from typing import Generic, Protocol, TypeAlias, TypeVar, cast, final
from uuid import uuid4

from mote_kernel.execution.engine.admission import admit_child_graph_input, project_graph_outputs
from mote_kernel.execution.engine.resume_input import materialize_node_input
from mote_kernel.execution.engine.session import GraphExecutionSession, consume_node_origin_cancellation
from mote_kernel.execution.errors import ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import GraphInputFrame, _public_values
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ExecutionRequestAttemptId,
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.invocation import (
    PlannedFence,
    PlannedResume,
    is_current_child_activation,
    project_resume_frames,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import StepRequest
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
    TaskResult,
    TaskSuccess,
    WaitingForChildren,
    _aborted_result,
    _awaiting_result,
    _commit_result,
    _completed_result,
    _partial_commit_error,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ChildBoundaryAvailabilityCoordinate,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
    _CompiledFamilyIdentity,
    _make_continuation,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    FailedGraphNode,
    FenceGraphExecution,
    GraphAbort,
    GraphAbortReason,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphRunCommand,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    ParentGraphActivation,
    graph_interrupt_id,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")
AwaitedT = TypeVar("AwaitedT")


async def wait_for_owner_task(
    task: asyncio.Task[AwaitedT],
    on_task_cancellation: Callable[[asyncio.CancelledError], None] | None = None,
) -> tuple[AwaitedT, asyncio.CancelledError | None]:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            current = cast(asyncio.Task[object], asyncio.current_task())
            if current.cancelling() == 0:
                break
            if cancellation is None:
                cancellation = error
    try:
        result = task.result()
    except asyncio.CancelledError as error:
        if on_task_cancellation is not None:
            on_task_cancellation(error)
        raise
    return result, cancellation


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
    commit: GraphCommit[GraphValueT],
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
    confirmed = await commit(transition)
    if type(confirmed) is not GraphRunState or confirmed != candidate:
        raise SnapshotMismatchError("commit must return the exact authoritative reducer successor")
    return confirmed


def scoped_commit(
    scope_run: ScopeRunCoordinate,
    commit: GraphCommit[GraphValueT] | None,
) -> GraphCommit[GraphValueT]:
    async def confirm(transition: GraphTransition[GraphValueT], /) -> GraphRunState:
        previous = transition.previous_state
        if (
            transition.scope != tuple(scope_run.scope)
            or transition.candidate_state.run_id != scope_run.graph_run_id
            or (previous is not None and previous.run_id != scope_run.graph_run_id)
        ):
            raise SnapshotMismatchError("owner commit received a transition for a different scoped graph run")
        if commit is None:
            return transition.candidate_state
        return await commit(transition)

    return confirm


_ChildTerminal: TypeAlias = CompletedChild[GraphValueT] | AbortedChild
_ChildPhase: TypeAlias = ActiveChild | AwaitingResume | _ChildTerminal[GraphValueT]
_OwnerEvidence: TypeAlias = tuple[tuple[ChildStateBinding, ...], ScopedFrameIndex[GraphValueT]]
_EvidenceReader: TypeAlias = Callable[[], _OwnerEvidence[GraphValueT]]
_EvidencePublisher: TypeAlias = Callable[[ChildStateBinding, ScopedFrameIndex[GraphValueT]], None]
_ChildWaitResult: TypeAlias = (
    tuple[
        GraphBoundary,
        _ChildTerminal[GraphValueT] | None,
        ConfirmedChildBoundary[GraphValueT] | None,
    ]
    | asyncio.CancelledError
)
_ChildHandle: TypeAlias = tuple[
    Callable[[], Awaitable[_ChildWaitResult[GraphValueT]]],
    Callable[[GraphAbortReason], Awaitable[None]],
    Callable[[], Awaitable[None]],
]
_ChildCall: TypeAlias = tuple[
    tuple[int, ...],
    ParentGraphActivation,
    _ChildPhase[GraphValueT],
    _ChildHandle[GraphValueT] | None,
]


async def _cleanup_unhanded_child(
    handle: _ChildHandle[GraphValueT],
    reason: GraphAbortReason,
    *,
    abort: bool,
) -> None:
    async def cleanup() -> None:
        if abort:
            with suppress(BaseException):
                await handle[1](reason)
        with suppress(BaseException):
            await handle[2]()

    cleanup_task = asyncio.create_task(cleanup())
    with suppress(BaseException):
        await wait_for_owner_task(cleanup_task)


def _merge_frames(
    indexes: tuple[ScopedFrameIndex[GraphValueT], ...],
) -> ScopedFrameIndex[GraphValueT]:
    merged: ScopedFrameIndex[GraphValueT] = ScopedFrameIndex()
    for index in indexes:
        for record in index.graph_inputs:
            merged = merged.add_graph_input(record)
        for record in index.publications:
            merged = merged.add_publication(record)
        for record in index.resume_inputs:
            merged = merged.add_resume_input(record)
        for record in index.child_boundaries:
            merged = merged.add_child_boundary(record)
    return merged


def _frames_for_owners(
    frames: ScopedFrameIndex[GraphValueT],
    bindings: tuple[ChildStateBinding, ...],
    owners: frozenset[ScopeRunCoordinate],
) -> ScopedFrameIndex[GraphValueT]:
    child_boundaries: list[ConfirmedChildBoundary[GraphValueT]] = []
    for record in frames.child_boundaries:
        binding = next((item for item in bindings if item.coordinate == record.coordinate.child_scope_run), None)
        if binding is None:
            raise SnapshotMismatchError(f"continuation has no child binding at {record.coordinate.child_scope_run!r}")
        if binding.parent_activation.scope_run in owners:
            child_boundaries.append(record)
    return ScopedFrameIndex(
        graph_inputs=tuple(record for record in frames.graph_inputs if record.coordinate.scope_run in owners),
        publications=tuple(
            record for record in frames.publications if record.coordinate.activation.scope_run in owners
        ),
        resume_inputs=tuple(
            record for record in frames.resume_inputs if record.coordinate.activation.scope_run in owners
        ),
        child_boundaries=tuple(child_boundaries),
    )


def _evidence_adapter(
    bindings: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
) -> tuple[_EvidencePublisher[GraphValueT], _EvidenceReader[GraphValueT]]:
    entries: list[tuple[ChildStateBinding, ScopedFrameIndex[GraphValueT]]] = [
        (
            binding,
            _frames_for_owners(frames, bindings, frozenset({binding.coordinate})),
        )
        for binding in bindings
    ]

    def publish(binding: ChildStateBinding, owner_frames: ScopedFrameIndex[GraphValueT]) -> None:
        index = next(
            (
                position
                for position, (existing, _frames) in enumerate(entries)
                if existing.coordinate == binding.coordinate
            ),
            None,
        )
        if index is None:
            entries.append((binding, owner_frames))
            return
        existing, _frames = entries[index]
        if existing.parent_activation != binding.parent_activation:
            raise SnapshotMismatchError("child evidence changed its parent activation")
        entries[index] = (binding, owner_frames)

    def read() -> _OwnerEvidence[GraphValueT]:
        canonical = tuple(sorted(entries, key=lambda entry: entry[0].coordinate))
        return (
            tuple(binding for binding, _frames in canonical),
            _merge_frames(tuple(owner_frames for _binding, owner_frames in canonical)),
        )

    return publish, read


class _GraphRun(Generic[GraphValueT]):
    """The sole live owner of one scoped graph run."""

    __slots__ = (
        "_children",
        "_commit",
        "_commit_origin_cancellation",
        "_executor",
        "_frames",
        "_graph",
        "_limits",
        "_node_origin_cancellation",
        "_parent_activation",
        "_position",
        "_publish_evidence",
        "_raw_commit",
        "_released",
        "_scope_run",
        "_session",
        "_state",
    )

    def __init__(
        self,
        graph: CompiledGraph[GraphValueT],
        scope_run: ScopeRunCoordinate,
        state: GraphRunState,
        frames: ScopedFrameIndex[GraphValueT],
        executor: GraphExecutor[GraphValueT],
        limits: ExecutionLimits,
        commit: GraphCommit[GraphValueT] | None,
        position: tuple[int, ...],
        parent_activation: StableActivation | None,
        evidence_publisher: _EvidencePublisher[GraphValueT],
    ) -> None:
        executor.validate_state(state)
        if state.run_id != scope_run.graph_run_id or graph.definition_scope != scope_run.scope:
            raise SnapshotMismatchError("graph owner state does not match its scoped definition")
        self._graph = graph
        self._scope_run = scope_run
        self._state = state
        self._frames = frames
        self._executor = executor
        self._limits = limits
        self._raw_commit = commit
        self._commit = scoped_commit(scope_run, commit)
        self._commit_origin_cancellation: asyncio.CancelledError | None = None
        self._position = position
        self._parent_activation = parent_activation
        self._publish_evidence = evidence_publisher
        self._children: list[_ChildCall[GraphValueT]] = []
        self._session: GraphExecutionSession[GraphValueT] | None = None
        self._node_origin_cancellation: asyncio.CancelledError | None = None
        self._released = False

    @property
    def state(self) -> GraphRunState:
        return self._state

    @property
    def frames(self) -> ScopedFrameIndex[GraphValueT]:
        return self._frames

    def _call_index(self, parent: ParentGraphActivation) -> int | None:
        return next((index for index, call in enumerate(self._children) if call[1] == parent), None)

    def _new_position(self, parent: ParentGraphActivation) -> tuple[int, ...]:
        if self._call_index(parent) is not None:
            raise ResultCollectionError("one parent activation cannot admit more than one child call")
        node_ids = tuple(self._graph.nodes)
        try:
            ordinal = node_ids.index(parent.node_id)
        except ValueError as error:
            raise ResultCollectionError("child activation is not part of the parent definition") from error
        generation = sum(1 for call in self._children if call[1].node_id == parent.node_id)
        return (*self._position, ordinal, generation)

    def child_position(self, parent: ParentGraphActivation) -> tuple[int, ...]:
        return self._new_position(parent)

    def accept_child_call(
        self,
        position: tuple[int, ...],
        parent: ParentGraphActivation,
        phase: _ChildPhase[GraphValueT],
        handle: _ChildHandle[GraphValueT] | None,
    ) -> None:
        if position != self._new_position(parent):
            raise ResultCollectionError("child call position does not match its parent activation")
        self._children.append((position, parent, phase, handle))
        self._children.sort(key=lambda call: call[0])

    def _replace_child(
        self,
        index: int,
        phase: _ChildPhase[GraphValueT],
        handle: _ChildHandle[GraphValueT] | None,
    ) -> None:
        position, parent, _old_phase, _old_handle = self._children[index]
        self._children[index] = (position, parent, phase, handle)

    def _child_projections(
        self,
    ) -> tuple[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild, ...]:
        projections: list[MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild] = []
        for node_id in pending_node_ids(self._state.frontier):
            if node_id not in self._graph.nested_graphs:
                continue
            parent = ParentGraphActivation(self._state.run_id, self._state.superstep, node_id)
            index = self._call_index(parent)
            if index is None:
                projections.append(MissingChild(parent))
                continue
            phase = self._children[index][2]
            if isinstance(phase, AwaitingResume | ActiveChild):
                projections.append(ActiveChild(parent))
            else:
                projections.append(phase)
        return tuple(projections)

    def _request(self) -> StepRequest[GraphValueT]:
        return StepRequest(
            self._state,
            self._scope_run,
            self._frames,
            ExecutionRequestAttemptId(str(uuid4())),
            self._child_projections(),
            self._limits,
        )

    def install_graph_input(self, input_frame: GraphInputFrame[GraphValueT]) -> None:
        coordinate: GraphInputAvailabilityCoordinate[GraphValueT] = GraphInputAvailabilityCoordinate(
            self._scope_run,
            self._graph.graph_input_descriptor.identity,
        )
        self._frames = self._frames.add_graph_input(AdmittedGraphInput(coordinate, input_frame))

    async def _transition(
        self,
        command: GraphRunCommand,
        result: TaskResult[GraphValueT] | None = None,
        *,
        confirmed_frames: ScopedFrameIndex[GraphValueT] | None = None,
        handoff_evidence: bool = False,
    ) -> GraphRunState:
        commit_task = asyncio.create_task(
            commit_transition(self._scope_run, self._state, command, result, self._commit)
        )
        confirmed, cancellation = await wait_for_owner_task(
            commit_task,
            self._mark_commit_origin_cancellation,
        )
        self._state = confirmed
        if confirmed_frames is not None:
            self._frames = confirmed_frames
        if handoff_evidence and self._parent_activation is not None:
            self.handoff_evidence()
        if cancellation is not None:
            raise cancellation
        return self._state

    async def apply_admission_fence(self, command: FenceGraphExecution) -> None:
        await self._transition(command, handoff_evidence=True)

    async def apply_admission_resume(self, planned: PlannedResume[GraphValueT]) -> None:
        candidate = reduce_graph_run(self._state, planned.prepared.command)
        confirmed_frames = project_resume_frames(self._frames, planned, candidate)
        await self._transition(
            planned.prepared.command,
            confirmed_frames=confirmed_frames,
            handoff_evidence=True,
        )

    def _mark_commit_origin_cancellation(self, error: asyncio.CancelledError) -> None:
        self._commit_origin_cancellation = error

    def consume_commit_origin_cancellation(self, error: asyncio.CancelledError) -> bool:
        if self._commit_origin_cancellation is not error:
            return False
        self._commit_origin_cancellation = None
        return True

    async def _fence(self, execution_token: GraphExecutionToken) -> None:
        await self._transition(FenceGraphExecution(self._state.revision, execution_token))

    def _install_terminal(
        self,
        index: int,
        boundary: ConfirmedChildBoundary[GraphValueT] | None,
    ) -> None:
        _position, _parent, phase, _handle = self._children[index]
        if not isinstance(phase, CompletedChild):
            if boundary is not None:
                raise ResultCollectionError("aborted child cannot provide a completed output boundary")
            return
        if boundary is None or boundary.frame != phase.output:
            raise ResultCollectionError("completed child did not provide its exact output boundary")
        self._frames = self._frames.add_child_boundary(boundary)

    async def _start_child(self, missing: MissingChild) -> None:
        parent = missing.parent
        if parent.run_id != self._state.run_id or parent.superstep != self._state.superstep:
            raise ResultCollectionError("missing child activation is stale or foreign")
        child_graph = self._graph.nested_graphs[parent.node_id]
        input_frame = materialize_node_input(
            self._graph,
            self._state,
            self._scope_run,
            self._frames,
            parent.node_id,
        )
        child_input = admit_child_graph_input(child_graph, input_frame)
        position = self.child_position(parent)
        construction = asyncio.create_task(
            _construct_fresh_child(
                self._scope_run,
                parent,
                child_graph,
                child_input,
                self._limits,
                self._raw_commit,
                position,
                self._publish_evidence,
            )
        )
        constructed, cancellation = await wait_for_owner_task(
            construction,
            self._mark_commit_origin_cancellation,
        )
        handle = constructed
        try:
            self.accept_child_call(position, parent, ActiveChild(parent), handle)
        except BaseException:
            await _cleanup_unhanded_child(
                handle,
                GraphAbortReason("nested graph owner handoff failed"),
                abort=True,
            )
            raise
        if cancellation is not None:
            raise cancellation

    async def _drive_child(self, index: int) -> None:
        _position, _parent, phase, handle = self._children[index]
        if handle is None or not isinstance(phase, ActiveChild):
            return
        child_result = await handle[0]()
        if isinstance(child_result, asyncio.CancelledError):
            self._mark_commit_origin_cancellation(child_result)
            raise child_result
        disposition, terminal, boundary = child_result
        if isinstance(disposition, AwaitingResume):
            if terminal is not None or boundary is not None:
                raise ResultCollectionError("awaiting child returned terminal evidence")
            self._replace_child(index, disposition, handle)
            return
        if terminal is None:
            raise ResultCollectionError("terminal child returned no terminal projection")
        if isinstance(disposition, CompletedGraph) and not isinstance(terminal, CompletedChild):
            raise ResultCollectionError("completed child returned a non-completed terminal projection")
        if isinstance(disposition, AbortedGraph) and not isinstance(terminal, AbortedChild):
            raise ResultCollectionError("aborted child returned a non-aborted terminal projection")
        self._replace_child(index, terminal, handle)
        self._install_terminal(index, boundary)

    async def _retire_child(self, result: TaskResult[GraphValueT]) -> None:
        if result.task.node_id not in self._graph.nested_graphs:
            return
        parent = ParentGraphActivation(result.task.run_id, result.task.superstep, result.task.node_id)
        index = self._call_index(parent)
        if index is None:
            raise ResultCollectionError("settled nested node has no admitted child call")
        _position, _parent, phase, handle = self._children[index]
        if not isinstance(phase, CompletedChild | AbortedChild):
            raise ResultCollectionError("nested node settlement requires one unretired terminal child")
        if handle is not None:
            await handle[2]()
        self._replace_child(index, phase, None)

    async def _consume_session(
        self,
        session: GraphExecutionSession[GraphValueT],
        execution_token: GraphExecutionToken,
    ) -> None:
        state = self._state
        async with session:
            while True:
                try:
                    completed = await session.next(state)
                except StopAsyncIteration:
                    return
                except asyncio.CancelledError as error:
                    if not consume_node_origin_cancellation(session, error):
                        raise
                    if self._parent_activation is None:
                        self._node_origin_cancellation = error
                        raise
                    await self._fence(execution_token)
                    await self._transition(
                        AbortGraphRun(self._state.revision, GraphAbortReason("nested graph node was cancelled"))
                    )
                    return
                except Exception:
                    await session.aclose()
                    await self._fence(execution_token)
                    raise
                confirmed = await self._transition(completed.command, completed.result)
                if isinstance(completed.result, TaskSuccess):
                    activation = StableActivation(
                        self._scope_run,
                        state.superstep,
                        completed.result.task.node_id,
                    )
                    coordinate: PublicationAvailabilityCoordinate[GraphValueT] = PublicationAvailabilityCoordinate(
                        activation,
                        self._graph.transition.publications[completed.result.task.node_id].identity,
                    )
                    self._frames = self._frames.add_publication(
                        ConfirmedPublication(
                            coordinate,
                            completed.result.output,
                            confirmed.revision,
                            ExecutionPublicationProvenance(completed.command.execution),
                        )
                    )
                await self._retire_child(completed.result)
                state = confirmed

    async def _execute_frontier(
        self,
        prepared: ExecutableFrontier,
        request: StepRequest[GraphValueT],
    ) -> None:
        claimed = await self._transition(prepared.claim.command)
        claimed_request = replace(request, state=claimed)
        execution = cast(GraphExecutionLease, claimed.execution)
        try:
            session = await self._executor.execute(prepared.claim, claimed_request)
        except Exception:
            await self._fence(execution.token)
            raise
        self._session = session
        try:
            await self._consume_session(session, execution.token)
        finally:
            self._session = None

    async def drive_quantum(self) -> GraphBoundary:
        while True:
            request = self._request()
            disposition = await self._executor.prepare(request)
            if isinstance(disposition, ReadyToResolve):
                await self._transition(disposition.command)
                continue
            if isinstance(disposition, ExecutableFrontier):
                await self._execute_frontier(disposition, request)
                continue
            if isinstance(disposition, WaitingForChildren):
                if disposition.missing:
                    for missing in disposition.missing:
                        await self._start_child(missing)
                    continue
                drove = False
                for active in disposition.active:
                    index = self._call_index(active.parent)
                    if index is None:
                        raise ResultCollectionError("active child projection has no admitted child call")
                    if isinstance(self._children[index][2], ActiveChild):
                        await self._drive_child(index)
                        drove = True
                if drove:
                    continue
                return AwaitingResume((), ())
            return disposition

    def handoff_evidence(self) -> None:
        if self._parent_activation is None:
            raise SnapshotMismatchError("root graph evidence cannot be handed off as a child binding")
        binding = ChildStateBinding(self._scope_run, self._parent_activation, self._state)
        self._publish_evidence(binding, self._frames)

    def freeze_root_evidence(
        self,
        evidence_reader: _EvidenceReader[GraphValueT],
    ) -> tuple[GraphRunState, tuple[ChildStateBinding, ...], ScopedFrameIndex[GraphValueT]]:
        if self._parent_activation is not None:
            raise SnapshotMismatchError("child graph evidence cannot be exported as the root")
        if any(isinstance(phase, ActiveChild) for _position, _parent, phase, _handle in self._children):
            raise SnapshotMismatchError("active child call has no handed-off export evidence")
        descendants, frames = evidence_reader()
        return self._state, descendants, _merge_frames((self._frames, frames))

    def consume_node_origin_cancellation(self, error: asyncio.CancelledError) -> bool:
        if self._node_origin_cancellation is not error:
            return False
        self._node_origin_cancellation = None
        return True

    def terminal_projection(self, parent: ParentGraphActivation) -> _ChildTerminal[GraphValueT]:
        if self._state.status is GraphRunStatus.COMPLETED:
            return CompletedChild(
                parent,
                project_graph_outputs(
                    self._graph,
                    self._scope_run,
                    self._state.superstep,
                    self._frames,
                ),
            )
        if self._state.status is GraphRunStatus.ABORTED and self._state.abort is not None:
            return AbortedChild(parent, self._state.abort.reason)
        raise ResultCollectionError("child evidence is not terminal")

    def terminal_boundary(
        self,
        parent: ParentGraphActivation,
        terminal: _ChildTerminal[GraphValueT],
    ) -> ConfirmedChildBoundary[GraphValueT] | None:
        activation = self._parent_activation
        expected_parent = (
            None
            if activation is None
            else ParentGraphActivation(
                activation.scope_run.graph_run_id,
                activation.superstep,
                activation.node_id,
            )
        )
        if parent != expected_parent:
            raise SnapshotMismatchError("child terminal handoff does not match its parent activation")
        if not isinstance(terminal, CompletedChild):
            return None
        coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
            self._scope_run,
            self._graph.graph_output_descriptor.identity,
        )
        return ConfirmedChildBoundary(coordinate, terminal.output)

    async def abort(self, reason: GraphAbortReason) -> None:
        errors: list[BaseException] = []
        for _position, _parent, phase, handle in self._children:
            if handle is None or isinstance(phase, CompletedChild | AbortedChild):
                continue
            try:
                await handle[1](reason)
            except BaseException as error:
                errors.append(error)
        if self._session is not None:
            try:
                await self._session.aclose()
            except BaseException as error:
                errors.append(error)
        if self._state.status is GraphRunStatus.RUNNING:
            if self._state.execution is not None:
                try:
                    await self._fence(self._state.execution.token)
                except BaseException as error:
                    errors.append(error)
            if self._state.execution is None:
                try:
                    await self._transition(AbortGraphRun(self._state.revision, reason))
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise errors[0]

    async def release(self) -> None:
        if self._released:
            return
        errors: list[BaseException] = []
        for index, call in enumerate(tuple(self._children)):
            position, parent, phase, handle = call
            if handle is None:
                continue
            try:
                await handle[2]()
                self._children[index] = (position, parent, phase, None)
            except BaseException as error:
                errors.append(error)
        if self._session is not None:
            try:
                await self._session.aclose()
            except BaseException as error:
                errors.append(error)
        self._session = None
        if errors:
            raise errors[0]
        self._released = True


OwnerHandoff: TypeAlias = tuple[_GraphRun[GraphValueT], _EvidenceReader[GraphValueT]]


def _admit_parent_activation(
    parent_scope_run: ScopeRunCoordinate,
    parent: ParentGraphActivation,
    child_graph: CompiledGraph[GraphValueT],
) -> StableActivation:
    if parent.run_id != parent_scope_run.graph_run_id or child_graph.definition_scope != (
        *parent_scope_run.scope,
        parent.node_id,
    ):
        raise SnapshotMismatchError("child construction does not match its parent activation")
    return StableActivation(parent_scope_run, parent.superstep, parent.node_id)


async def _construct_fresh_child(
    parent_scope_run: ScopeRunCoordinate,
    parent: ParentGraphActivation,
    child_graph: CompiledGraph[GraphValueT],
    child_input: GraphInputFrame[GraphValueT],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
    position: tuple[int, ...],
    evidence_publisher: _EvidencePublisher[GraphValueT],
) -> _ChildHandle[GraphValueT]:
    activation = _admit_parent_activation(parent_scope_run, parent, child_graph)
    coordinate = child_scope_run_for_activation(parent_scope_run, parent)
    child_commit = scoped_commit(coordinate, commit)
    command = project_start_graph_command(child_graph, coordinate.graph_run_id, parent)
    child_state = await commit_transition(coordinate, None, command, None, child_commit)
    child: _GraphRun[GraphValueT] | None = None
    try:
        child = _GraphRun(
            child_graph,
            coordinate,
            child_state,
            ScopedFrameIndex(),
            GraphExecutor(child_graph),
            limits,
            commit,
            position,
            activation,
            evidence_publisher,
        )
        child.install_graph_input(child_input)
        return _opaque_handle(child, parent)
    except BaseException:

        async def cleanup_candidate() -> None:
            reason = GraphAbortReason("nested graph owner construction failed")
            if child is None:
                await commit_transition(
                    coordinate,
                    child_state,
                    AbortGraphRun(child_state.revision, reason),
                    None,
                    child_commit,
                )
                return
            with suppress(BaseException):
                await child.abort(reason)
            with suppress(BaseException):
                await child.release()

        cleanup_task = asyncio.create_task(cleanup_candidate())
        with suppress(BaseException):
            await wait_for_owner_task(cleanup_task)
        raise


def _opaque_handle(
    child: _GraphRun[GraphValueT],
    parent: ParentGraphActivation,
) -> _ChildHandle[GraphValueT]:
    owner: _GraphRun[GraphValueT] | None = child
    handed_off = False

    def require_owner() -> _GraphRun[GraphValueT]:
        if owner is None:
            raise ResultCollectionError("child call handle was already released")
        return owner

    async def drive() -> _ChildWaitResult[GraphValueT]:
        nonlocal handed_off
        current = require_owner()
        if handed_off:
            raise ResultCollectionError("child call evidence can only be handed off once")
        try:
            disposition = await current.drive_quantum()
        except asyncio.CancelledError as error:
            if current.consume_commit_origin_cancellation(error):
                return error
            raise
        if isinstance(disposition, AwaitingResume):
            current.handoff_evidence()
            handed_off = True
            return disposition, None, None
        terminal = current.terminal_projection(parent)
        boundary = current.terminal_boundary(parent, terminal)
        current.handoff_evidence()
        handed_off = True
        return disposition, terminal, boundary

    async def abort(reason: GraphAbortReason) -> None:
        if owner is not None:
            await owner.abort(reason)

    async def release() -> None:
        nonlocal owner
        if owner is None:
            return
        await owner.release()
        owner = None

    return drive, abort, release


def _executor_at(
    executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[GraphValueT]], ...],
    coordinate: ScopeRunCoordinate,
) -> GraphExecutor[GraphValueT]:
    executor = next((candidate for scope_run, candidate in executors if scope_run == coordinate), None)
    if executor is None:
        raise SnapshotMismatchError(f"invocation has no executor at {coordinate!r}")
    return executor


async def admit_continued_root(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    child_states: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
    executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[GraphValueT]], ...],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
    fences: tuple[PlannedFence, ...],
    resumes: tuple[PlannedResume[GraphValueT], ...],
    family_identity: _CompiledFamilyIdentity,
    *,
    recovered: bool,
) -> OwnerHandoff[GraphValueT]:
    scope_run = root_scope_run(state.run_id)
    evidence_publisher, evidence_reader = _evidence_adapter(child_states, frames)
    root: _GraphRun[GraphValueT] | None = None
    confirmed_prefix = False
    transition_attempted = False
    failed_scope: tuple[str, ...] | None = None

    async def apply_admission(
        owner: _GraphRun[GraphValueT],
        fence: PlannedFence | None,
        resume: PlannedResume[GraphValueT] | None,
        owner_scope: tuple[str, ...],
    ) -> None:
        nonlocal confirmed_prefix, failed_scope, transition_attempted
        try:
            if fence is not None:
                transition_attempted = True
                await owner.apply_admission_fence(fence.command)
                confirmed_prefix = True
            if resume is not None:
                transition_attempted = True
                await owner.apply_admission_resume(resume)
                confirmed_prefix = True
        except (Exception, asyncio.CancelledError):
            failed_scope = owner_scope
            raise

    async def construct_child(
        parent: ParentGraphActivation,
        binding: ChildStateBinding,
        child_graph: CompiledGraph[GraphValueT],
        position: tuple[int, ...],
        fence: PlannedFence | None,
        resume: PlannedResume[GraphValueT] | None,
    ) -> _ChildHandle[GraphValueT]:
        nonlocal failed_scope
        child: _GraphRun[GraphValueT] | None = None
        try:
            child_frames = _frames_for_owners(frames, child_states, frozenset({binding.coordinate}))
            child = _GraphRun(
                child_graph,
                binding.coordinate,
                binding.state,
                child_frames,
                _executor_at(executors, binding.coordinate),
                limits,
                commit,
                position,
                binding.parent_activation,
                evidence_publisher,
            )
            await apply_admission(child, fence, resume, tuple(binding.coordinate.scope))
            await admit_children(child, child_graph, binding.coordinate, binding.state)
            return _opaque_handle(child, parent)
        except BaseException:
            if failed_scope is None:
                failed_scope = tuple(binding.coordinate.scope)

            async def cleanup_candidate(candidate: _GraphRun[GraphValueT] | None) -> None:
                reason = GraphAbortReason("continued graph owner construction failed")
                if candidate is None:
                    if not transition_attempted:
                        with suppress(BaseException):
                            await commit_transition(
                                binding.coordinate,
                                binding.state,
                                AbortGraphRun(binding.state.revision, reason),
                                None,
                                scoped_commit(binding.coordinate, commit),
                            )
                    return
                if not transition_attempted:
                    with suppress(BaseException):
                        await candidate.abort(reason)
                with suppress(BaseException):
                    await candidate.release()

            cleanup_task = asyncio.create_task(cleanup_candidate(child))
            with suppress(BaseException):
                await wait_for_owner_task(cleanup_task)
            raise

    async def admit_children(
        owner: _GraphRun[GraphValueT],
        owner_graph: CompiledGraph[GraphValueT],
        owner_scope_run: ScopeRunCoordinate,
        owner_state: GraphRunState,
    ) -> None:
        nonlocal failed_scope
        direct_candidates = tuple(
            binding for binding in child_states if binding.parent_activation.scope_run == owner_scope_run
        )
        admitted: list[tuple[ChildStateBinding, ParentGraphActivation, CompiledGraph[GraphValueT]]] = []
        for binding in direct_candidates:
            activation = binding.parent_activation
            if not is_current_child_activation(owner_state, activation):
                continue
            parent = ParentGraphActivation(
                owner_state.run_id,
                activation.superstep,
                activation.node_id,
            )
            admitted.append((binding, parent, owner_graph.nested_graphs[activation.node_id]))
        direct = tuple(
            sorted(
                admitted,
                key=lambda item: tuple(owner_graph.nodes).index(item[1].node_id),
            )
        )
        for binding, parent, child_graph in direct:
            position = owner.child_position(parent)
            if binding.state.status is not GraphRunStatus.RUNNING:
                if binding.state.status is GraphRunStatus.COMPLETED:
                    availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = (
                        ChildBoundaryAvailabilityCoordinate(
                            binding.coordinate,
                            child_graph.graph_output_descriptor.identity,
                        )
                    )
                    phase: _ChildPhase[GraphValueT] = CompletedChild(
                        parent,
                        owner.frames.lookup(availability).frame,
                    )
                else:
                    abort = cast(GraphAbort, binding.state.abort)
                    phase = AbortedChild(parent, abort.reason)
                owner.accept_child_call(position, parent, phase, None)
                continue

            fence = next((candidate for candidate in fences if candidate.scope_run == binding.coordinate), None)
            resume = next(
                (candidate for candidate in resumes if candidate.scope_run == binding.coordinate),
                None,
            )
            handle = await construct_child(parent, binding, child_graph, position, fence, resume)
            try:
                owner.accept_child_call(position, parent, ActiveChild(parent), handle)
            except BaseException:
                failed_scope = tuple(binding.coordinate.scope)
                await _cleanup_unhanded_child(
                    handle,
                    GraphAbortReason("continued graph owner handoff failed"),
                    abort=not transition_attempted,
                )
                raise

    try:
        root_frames = _frames_for_owners(frames, child_states, frozenset({scope_run}))
        root = _GraphRun(
            graph,
            scope_run,
            state,
            root_frames,
            _executor_at(executors, scope_run),
            limits,
            commit,
            (),
            None,
            evidence_publisher,
        )
        root_fence = next((candidate for candidate in fences if candidate.scope_run == scope_run), None)
        root_resume = next((candidate for candidate in resumes if candidate.scope_run == scope_run), None)
        await apply_admission(root, root_fence, root_resume, ())
        await admit_children(root, graph, scope_run, state)
        return root, evidence_reader
    except BaseException as primary:
        if failed_scope is None:
            failed_scope = ()
        if root is not None and isinstance(primary, (Exception, asyncio.CancelledError)) and transition_attempted:
            if not confirmed_prefix:
                with suppress(BaseException):
                    await root.release()
                raise primary from None
            confirmed_children, child_frames = evidence_reader()
            continuation = _make_continuation(
                family_identity,
                root.state,
                confirmed_children,
                _merge_frames((root.frames, child_frames)),
                recovered=recovered,
            )
            partial = _partial_commit_error(
                root.state,
                continuation,
                primary,
                failed_scope,
            )
            with suppress(BaseException):
                await root.release()
            raise partial from primary

        async def cleanup_root() -> None:
            reason = GraphAbortReason("continued graph owner construction failed")
            if root is not None:
                if not transition_attempted:
                    with suppress(BaseException):
                        await root.abort(reason)
                with suppress(BaseException):
                    await root.release()
                return
            if not transition_attempted and state.status is GraphRunStatus.RUNNING:
                with suppress(BaseException):
                    await commit_transition(
                        scope_run,
                        state,
                        AbortGraphRun(state.revision, reason),
                        None,
                        scoped_commit(scope_run, commit),
                    )

        cleanup_task = asyncio.create_task(cleanup_root())
        with suppress(BaseException):
            await wait_for_owner_task(cleanup_task)
        raise


async def fresh_root(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    state: GraphRunState,
    input_frame: GraphInputFrame[GraphValueT],
    executor: GraphExecutor[GraphValueT],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> OwnerHandoff[GraphValueT]:
    evidence_publisher, evidence_reader = _evidence_adapter((), ScopedFrameIndex())
    root: _GraphRun[GraphValueT] | None = None
    try:
        root = _GraphRun(
            graph,
            scope_run,
            state,
            ScopedFrameIndex(),
            executor,
            limits,
            commit,
            (),
            None,
            evidence_publisher,
        )
        root.install_graph_input(input_frame)
        return root, evidence_reader
    except BaseException:

        async def cleanup_root() -> None:
            if root is None:
                with suppress(BaseException):
                    await commit_transition(
                        scope_run,
                        state,
                        AbortGraphRun(
                            state.revision,
                            GraphAbortReason("root graph owner construction failed"),
                        ),
                        None,
                        scoped_commit(scope_run, commit),
                    )
                return
            with suppress(BaseException):
                await root.abort(GraphAbortReason("root graph owner construction failed"))
            with suppress(BaseException):
                await root.release()

        cleanup_task = asyncio.create_task(cleanup_root())
        with suppress(BaseException):
            await wait_for_owner_task(cleanup_task)
        raise


async def drive_root(root: _GraphRun[GraphValueT]) -> GraphBoundary:
    return await root.drive_quantum()


def _project_result_views(
    states: tuple[tuple[tuple[str, ...], GraphRunState], ...],
) -> tuple[tuple[GraphFailureView, ...], tuple[GraphInterruptView, ...]]:
    failures: list[GraphFailureView] = []
    interrupts: list[GraphInterruptView] = []
    for scope, state in states:
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
    family_identity: _CompiledFamilyIdentity,
    root: _GraphRun[GraphValueT],
    evidence_reader: _EvidenceReader[GraphValueT],
    disposition: GraphBoundary,
    *,
    recovered: bool,
) -> GraphResult[GraphValueT]:
    if type(disposition) not in (CompletedGraph, AbortedGraph, AwaitingResume):
        raise SnapshotMismatchError("graph driver returned an unsupported boundary")
    state, child_states, frames = root.freeze_root_evidence(evidence_reader)
    continuation = _make_continuation(
        family_identity,
        state,
        child_states,
        frames,
        recovered=recovered,
    )
    if isinstance(disposition, CompletedGraph):
        view = project_graph_outputs(graph, root_scope_run(state.run_id), state.superstep, frames)
        return _completed_result(state, continuation, _public_values(view))
    if isinstance(disposition, AbortedGraph):
        if state.abort is None:
            raise SnapshotMismatchError("aborted root state is missing its canonical abort")
        return _aborted_result(state, continuation, GraphAbortView((), state.abort.reason))
    scoped_states = (
        ((), state),
        *((tuple(binding.coordinate.scope), binding.state) for binding in child_states),
    )
    failures, interrupts = _project_result_views(scoped_states)
    return _awaiting_result(state, continuation, failures, interrupts)


__all__: list[str] = []

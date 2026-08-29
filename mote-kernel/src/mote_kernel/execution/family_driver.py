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
    GraphAbortReason,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFrontierStatus,
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
_DriveChild: TypeAlias = Callable[[], Awaitable[GraphBoundary]]
_AbortChild: TypeAlias = Callable[[GraphAbortReason], Awaitable[None]]
_ReleaseChild: TypeAlias = Callable[[], Awaitable[None]]
_ConsumeChild: TypeAlias = Callable[
    [],
    tuple[
        _ChildTerminal[GraphValueT],
        _EvidenceReader[GraphValueT],
        ConfirmedChildBoundary[GraphValueT] | None,
    ],
]
_ChildHandle: TypeAlias = tuple[
    _DriveChild,
    _AbortChild,
    _ReleaseChild,
    _ConsumeChild[GraphValueT],
    _EvidenceReader[GraphValueT],
]
_ChildCall: TypeAlias = tuple[
    tuple[int, ...],
    ParentGraphActivation,
    _ChildPhase[GraphValueT],
    _ChildHandle[GraphValueT] | None,
    _EvidenceReader[GraphValueT] | None,
]


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


def _binding_at(
    bindings: tuple[ChildStateBinding, ...],
    coordinate: ScopeRunCoordinate,
) -> ChildStateBinding:
    binding = next((item for item in bindings if item.coordinate == coordinate), None)
    if binding is None:
        raise SnapshotMismatchError(f"continuation has no child binding at {coordinate!r}")
    return binding


def _frames_for_owners(
    frames: ScopedFrameIndex[GraphValueT],
    bindings: tuple[ChildStateBinding, ...],
    owners: frozenset[ScopeRunCoordinate],
) -> ScopedFrameIndex[GraphValueT]:
    return ScopedFrameIndex(
        graph_inputs=tuple(record for record in frames.graph_inputs if record.coordinate.scope_run in owners),
        publications=tuple(
            record for record in frames.publications if record.coordinate.activation.scope_run in owners
        ),
        resume_inputs=tuple(
            record for record in frames.resume_inputs if record.coordinate.activation.scope_run in owners
        ),
        child_boundaries=tuple(
            record
            for record in frames.child_boundaries
            if _binding_at(bindings, record.coordinate.child_scope_run).parent_activation.scope_run in owners
        ),
    )


def _subtree_bindings(
    root: ScopeRunCoordinate,
    bindings: tuple[ChildStateBinding, ...],
) -> tuple[ChildStateBinding, ...]:
    selected = {root}
    changed = True
    while changed:
        changed = False
        for binding in bindings:
            if binding.parent_activation.scope_run in selected and binding.coordinate not in selected:
                selected.add(binding.coordinate)
                changed = True
    return tuple(
        sorted(
            (binding for binding in bindings if binding.coordinate in selected),
            key=lambda item: item.coordinate,
        )
    )


def _frozen_reader(
    bindings: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
) -> _EvidenceReader[GraphValueT]:
    def read() -> _OwnerEvidence[GraphValueT]:
        return bindings, frames

    return read


class _GraphRun(Generic[GraphValueT]):
    """The sole live owner of one scoped graph run."""

    __slots__ = (
        "_children",
        "_commit",
        "_executor",
        "_frames",
        "_graph",
        "_limits",
        "_node_origin_cancellation",
        "_parent_activation",
        "_position",
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
        self._position = position
        self._parent_activation = parent_activation
        self._children: list[_ChildCall[GraphValueT]] = []
        self._session: GraphExecutionSession[GraphValueT] | None = None
        self._node_origin_cancellation: asyncio.CancelledError | None = None
        self._released = False

    @property
    def state(self) -> GraphRunState:
        return self._state

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

    def _replace_child(
        self,
        index: int,
        phase: _ChildPhase[GraphValueT],
        handle: _ChildHandle[GraphValueT] | None,
        evidence: _EvidenceReader[GraphValueT] | None,
    ) -> None:
        position, parent, _old_phase, _old_handle, _old_evidence = self._children[index]
        self._children[index] = (position, parent, phase, handle, evidence)

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
    ) -> GraphRunState:
        confirmed = await commit_transition(self._scope_run, self._state, command, result, self._commit)
        self._state = confirmed
        return confirmed

    async def _fence(self, execution_token: GraphExecutionToken) -> None:
        await self._transition(FenceGraphExecution(self._state.revision, execution_token))

    def _install_terminal(
        self,
        index: int,
        boundary: ConfirmedChildBoundary[GraphValueT] | None,
    ) -> None:
        _position, parent, phase, handle, evidence = self._children[index]
        if not isinstance(phase, CompletedChild):
            if boundary is not None:
                raise ResultCollectionError("aborted child cannot provide a completed output boundary")
            return
        if boundary is None or boundary.frame != phase.output:
            raise ResultCollectionError("completed child did not provide its exact output boundary")
        child_graph = self._graph.nested_graphs[parent.node_id]
        availability = boundary.coordinate
        if (
            availability.child_scope_run.scope != child_graph.definition_scope
            or availability.descriptor != child_graph.graph_output_descriptor.identity
        ):
            raise SnapshotMismatchError("completed child boundary does not match its parent definition")
        if self._frames.has_child_boundary(availability):
            existing = self._frames.lookup(availability)
            if existing.frame != phase.output:
                raise SnapshotMismatchError("completed child output does not match its confirmed boundary")
            return
        self._frames = self._frames.add_child_boundary(ConfirmedChildBoundary(availability, phase.output))
        self._replace_child(index, phase, handle, evidence)

    async def _start_child(self, missing: MissingChild) -> None:
        parent = missing.parent
        if parent.run_id != self._state.run_id or parent.superstep != self._state.superstep:
            raise ResultCollectionError("missing child activation is stale or foreign")
        position = self._new_position(parent)
        child_graph = self._graph.nested_graphs[parent.node_id]
        input_frame = materialize_node_input(
            self._graph,
            self._state,
            self._scope_run,
            self._frames,
            parent.node_id,
        )
        child_input = admit_child_graph_input(child_graph, input_frame)
        coordinate = child_scope_run_for_activation(self._scope_run, parent)
        child_executor = GraphExecutor(child_graph)
        child_commit = scoped_commit(coordinate, self._raw_commit)
        command = project_start_graph_command(child_graph, coordinate.graph_run_id, parent)
        child_state = await commit_transition(coordinate, None, command, None, child_commit)
        activation = StableActivation(self._scope_run, parent.superstep, parent.node_id)
        child: _GraphRun[GraphValueT] | None = None
        try:
            child = _GraphRun(
                child_graph,
                coordinate,
                child_state,
                ScopedFrameIndex(),
                child_executor,
                self._limits,
                self._raw_commit,
                position,
                activation,
            )
            child.install_graph_input(child_input)
            handle = _opaque_handle(child, parent)
            self._children.append((position, parent, ActiveChild(parent), handle, None))
            self._children.sort(key=lambda call: call[0])
        except BaseException:

            async def cleanup_candidate() -> None:
                if child is None:
                    await commit_transition(
                        coordinate,
                        child_state,
                        AbortGraphRun(
                            child_state.revision,
                            GraphAbortReason("nested graph owner construction failed"),
                        ),
                        None,
                        child_commit,
                    )
                    return
                with suppress(BaseException):
                    await child.abort(GraphAbortReason("nested graph owner construction failed"))
                with suppress(BaseException):
                    await child.release()

            cleanup_task = asyncio.create_task(cleanup_candidate())
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    continue
            with suppress(BaseException):
                cleanup_task.result()
            raise

    async def _drive_child(self, index: int) -> None:
        _position, _parent, phase, handle, _evidence = self._children[index]
        if handle is None or not isinstance(phase, ActiveChild):
            return
        disposition = await handle[0]()
        if isinstance(disposition, AwaitingResume):
            self._replace_child(index, disposition, handle, None)
            return
        terminal, evidence, boundary = handle[3]()
        if isinstance(disposition, CompletedGraph) and not isinstance(terminal, CompletedChild):
            raise ResultCollectionError("completed child returned a non-completed terminal projection")
        if isinstance(disposition, AbortedGraph) and not isinstance(terminal, AbortedChild):
            raise ResultCollectionError("aborted child returned a non-aborted terminal projection")
        self._replace_child(index, terminal, handle, evidence)
        self._install_terminal(index, boundary)

    async def _retire_child(self, result: TaskResult[GraphValueT]) -> None:
        if result.task.node_id not in self._graph.nested_graphs:
            return
        parent = ParentGraphActivation(result.task.run_id, result.task.superstep, result.task.node_id)
        index = self._call_index(parent)
        if index is None:
            raise ResultCollectionError("settled nested node has no admitted child call")
        _position, _parent, phase, handle, evidence = self._children[index]
        if not isinstance(phase, CompletedChild | AbortedChild) or evidence is None:
            raise ResultCollectionError("nested node settlement requires one unretired terminal child")
        if handle is not None:
            await handle[2]()
        self._replace_child(index, phase, None, evidence)

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

    def _descendant_evidence(self) -> _OwnerEvidence[GraphValueT]:
        bindings: list[ChildStateBinding] = []
        indexes: list[ScopedFrameIndex[GraphValueT]] = [self._frames]
        for _position, _parent, _phase, handle, evidence in self._children:
            reader = evidence if evidence is not None else handle[4] if handle is not None else None
            if reader is None:
                raise SnapshotMismatchError("child call has no export evidence")
            child_bindings, child_frames = reader()
            bindings.extend(child_bindings)
            indexes.append(child_frames)
        return tuple(sorted(bindings, key=lambda binding: binding.coordinate)), _merge_frames(tuple(indexes))

    def freeze_child_evidence(self) -> _OwnerEvidence[GraphValueT]:
        if self._parent_activation is None:
            raise SnapshotMismatchError("root graph evidence cannot be exported as a child binding")
        descendants, frames = self._descendant_evidence()
        binding = ChildStateBinding(self._scope_run, self._parent_activation, self._state)
        return (binding, *descendants), frames

    def freeze_root_evidence(
        self,
    ) -> tuple[GraphRunState, tuple[ChildStateBinding, ...], ScopedFrameIndex[GraphValueT]]:
        if self._parent_activation is not None:
            raise SnapshotMismatchError("child graph evidence cannot be exported as the root")
        descendants, frames = self._descendant_evidence()
        return self._state, descendants, frames

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
        terminal: _ChildTerminal[GraphValueT],
    ) -> ConfirmedChildBoundary[GraphValueT] | None:
        if not isinstance(terminal, CompletedChild):
            return None
        coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
            self._scope_run,
            self._graph.graph_output_descriptor.identity,
        )
        return ConfirmedChildBoundary(coordinate, terminal.output)

    async def admit_existing_children(
        self,
        bindings: tuple[ChildStateBinding, ...],
        frames: ScopedFrameIndex[GraphValueT],
        executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[GraphValueT]], ...],
    ) -> None:
        direct_candidates = tuple(
            binding for binding in bindings if binding.parent_activation.scope_run == self._scope_run
        )
        if any(binding.parent_activation.node_id not in self._graph.nested_graphs for binding in direct_candidates):
            raise SnapshotMismatchError("continuation child binding has no parent nested definition")
        direct = tuple(
            sorted(
                direct_candidates,
                key=lambda binding: (
                    tuple(self._graph.nodes).index(binding.parent_activation.node_id),
                    binding.parent_activation.superstep,
                ),
            )
        )
        for binding in direct:
            activation = binding.parent_activation
            parent = ParentGraphActivation(
                activation.scope_run.graph_run_id,
                activation.superstep,
                activation.node_id,
            )
            child_graph = self._graph.nested_graphs[parent.node_id]
            position = self._new_position(parent)
            if binding.state.status is GraphRunStatus.RUNNING:
                child: _GraphRun[GraphValueT] | None = None
                try:
                    child = _GraphRun(
                        child_graph,
                        binding.coordinate,
                        binding.state,
                        _frames_for_owners(frames, bindings, frozenset({binding.coordinate})),
                        _executor_at(executors, binding.coordinate),
                        self._limits,
                        self._raw_commit,
                        position,
                        activation,
                    )
                    await child.admit_existing_children(bindings, frames, executors)
                    handle = _opaque_handle(child, parent)
                except BaseException:

                    async def cleanup_candidate(
                        candidate_owner: _GraphRun[GraphValueT] | None,
                        candidate_binding: ChildStateBinding,
                    ) -> None:
                        reason = GraphAbortReason("continued graph owner construction failed")
                        if candidate_owner is not None:
                            with suppress(BaseException):
                                await candidate_owner.abort(reason)
                            with suppress(BaseException):
                                await candidate_owner.release()
                            return
                        subtree = _subtree_bindings(candidate_binding.coordinate, bindings)
                        pending = tuple(
                            sorted(
                                subtree,
                                key=lambda subtree_binding: (
                                    -len(subtree_binding.coordinate.scope),
                                    subtree_binding.coordinate,
                                ),
                            )
                        )
                        for candidate in pending:
                            current = candidate.state
                            if current.status is not GraphRunStatus.RUNNING:
                                continue
                            candidate_commit = scoped_commit(candidate.coordinate, self._raw_commit)
                            with suppress(BaseException):
                                await commit_transition(
                                    candidate.coordinate,
                                    current,
                                    AbortGraphRun(current.revision, reason),
                                    None,
                                    candidate_commit,
                                )

                    cleanup_task = asyncio.create_task(cleanup_candidate(child, binding))
                    while not cleanup_task.done():
                        try:
                            await asyncio.shield(cleanup_task)
                        except asyncio.CancelledError:
                            continue
                    with suppress(BaseException):
                        cleanup_task.result()
                    raise
                status = frontier_status(binding.state.frontier)
                phase: _ChildPhase[GraphValueT] = (
                    AwaitingResume((), ()) if status is GraphFrontierStatus.AWAITING_RESUME else ActiveChild(parent)
                )
                self._children.append((position, parent, phase, handle, None))
                continue
            subtree = _subtree_bindings(binding.coordinate, bindings)
            subtree_owners = frozenset(item.coordinate for item in subtree)
            evidence = _frozen_reader(subtree, _frames_for_owners(frames, bindings, subtree_owners))
            if binding.state.status is GraphRunStatus.COMPLETED:
                availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
                    binding.coordinate,
                    child_graph.graph_output_descriptor.identity,
                )
                phase = CompletedChild(parent, self._frames.lookup(availability).frame)
            elif binding.state.abort is not None:
                phase = AbortedChild(parent, binding.state.abort.reason)
            else:
                raise SnapshotMismatchError("terminal child binding has no canonical outcome")
            self._children.append((position, parent, phase, None, evidence))
        self._children.sort(key=lambda call: call[0])

    async def abort(self, reason: GraphAbortReason) -> None:
        errors: list[BaseException] = []
        for _position, _parent, phase, handle, _evidence in self._children:
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
            position, parent, phase, handle, evidence = call
            if handle is None:
                continue
            if evidence is None:
                try:
                    evidence = handle[4]
                    evidence()
                except BaseException as error:
                    errors.append(error)
            try:
                await handle[2]()
                self._children[index] = (position, parent, phase, None, evidence)
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


def _opaque_handle(
    child: _GraphRun[GraphValueT],
    parent: ParentGraphActivation,
) -> _ChildHandle[GraphValueT]:
    owner: _GraphRun[GraphValueT] | None = child
    frozen: _OwnerEvidence[GraphValueT] | None = None
    consumed = False

    def require_owner() -> _GraphRun[GraphValueT]:
        if owner is None:
            raise ResultCollectionError("child call handle was already released")
        return owner

    async def drive() -> GraphBoundary:
        return await require_owner().drive_quantum()

    async def abort(reason: GraphAbortReason) -> None:
        if owner is not None:
            await owner.abort(reason)

    async def release() -> None:
        nonlocal owner
        if owner is None:
            return
        await owner.release()
        owner = None

    def export() -> _OwnerEvidence[GraphValueT]:
        nonlocal frozen
        if frozen is None:
            frozen = require_owner().freeze_child_evidence()
        return frozen

    def consume() -> tuple[
        _ChildTerminal[GraphValueT],
        _EvidenceReader[GraphValueT],
        ConfirmedChildBoundary[GraphValueT] | None,
    ]:
        nonlocal consumed
        if consumed:
            raise ResultCollectionError("terminal child evidence can only be consumed once")
        current = require_owner()
        terminal = current.terminal_projection(parent)
        boundary = current.terminal_boundary(terminal)
        export()
        consumed = True
        return terminal, export, boundary

    return drive, abort, release, consume, export


def _executor_at(
    executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[GraphValueT]], ...],
    coordinate: ScopeRunCoordinate,
) -> GraphExecutor[GraphValueT]:
    executor = next((candidate for scope_run, candidate in executors if scope_run == coordinate), None)
    if executor is None:
        raise SnapshotMismatchError(f"invocation has no executor at {coordinate!r}")
    return executor


async def admit_root(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    child_states: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
    executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[GraphValueT]], ...],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> _GraphRun[GraphValueT]:
    scope_run = ScopeRunCoordinate((), state.run_id)
    root: _GraphRun[GraphValueT] | None = None
    try:
        root = _GraphRun(
            graph,
            scope_run,
            state,
            _frames_for_owners(frames, child_states, frozenset({scope_run})),
            _executor_at(executors, scope_run),
            limits,
            commit,
            (),
            None,
        )
        await root.admit_existing_children(child_states, frames, executors)
        return root
    except BaseException:

        async def cleanup_root() -> None:
            reason = GraphAbortReason("continued graph owner construction failed")
            if root is not None:
                with suppress(BaseException):
                    await root.abort(reason)
                with suppress(BaseException):
                    await root.release()
                return
            pending = tuple(
                sorted(
                    child_states,
                    key=lambda binding: (-len(binding.coordinate.scope), binding.coordinate),
                )
            )
            for binding in pending:
                current = binding.state
                if current.status is not GraphRunStatus.RUNNING:
                    continue
                binding_commit = scoped_commit(binding.coordinate, commit)
                with suppress(BaseException):
                    await commit_transition(
                        binding.coordinate,
                        current,
                        AbortGraphRun(current.revision, reason),
                        None,
                        binding_commit,
                    )
            root_commit = scoped_commit(scope_run, commit)
            with suppress(BaseException):
                if state.status is GraphRunStatus.RUNNING:
                    await commit_transition(
                        scope_run,
                        state,
                        AbortGraphRun(state.revision, reason),
                        None,
                        root_commit,
                    )

        cleanup_task = asyncio.create_task(cleanup_root())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        with suppress(BaseException):
            cleanup_task.result()
        raise


async def fresh_root(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    state: GraphRunState,
    input_frame: GraphInputFrame[GraphValueT],
    executor: GraphExecutor[GraphValueT],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> _GraphRun[GraphValueT]:
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
        )
        root.install_graph_input(input_frame)
        return root
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
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        with suppress(BaseException):
            cleanup_task.result()
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
    disposition: GraphBoundary,
    *,
    recovered: bool,
) -> GraphResult[GraphValueT]:
    if type(disposition) not in (CompletedGraph, AbortedGraph, AwaitingResume):
        raise SnapshotMismatchError("graph driver returned an unsupported boundary")
    state, child_states, frames = root.freeze_root_evidence()
    continuation = _make_continuation(
        family_identity,
        state,
        child_states,
        frames,
        recovered=recovered,
    )
    if isinstance(disposition, CompletedGraph):
        view = project_graph_outputs(graph, ScopeRunCoordinate((), state.run_id), state.superstep, frames)
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

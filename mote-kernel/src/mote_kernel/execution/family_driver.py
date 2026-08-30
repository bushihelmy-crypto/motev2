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
    GraphAbort,
    GraphAbortReason,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphFrontierStatus,
    GraphRunCommand,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    InterruptedGraphNode,
    ParentGraphActivation,
    frontier_status,
    graph_interrupt_id,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")
AwaitedT = TypeVar("AwaitedT")


async def wait_for_owner_task(
    task: asyncio.Task[AwaitedT],
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
    return task.result(), cancellation


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
_ChildWaitResult: TypeAlias = tuple[
    GraphBoundary,
    _ChildTerminal[GraphValueT] | None,
    ConfirmedChildBoundary[GraphValueT] | None,
]
_DriveChild: TypeAlias = Callable[[], Awaitable[_ChildWaitResult[GraphValueT]]]
_AbortChild: TypeAlias = Callable[[GraphAbortReason], Awaitable[None]]
_ReleaseChild: TypeAlias = Callable[[], Awaitable[None]]
_ChildHandle: TypeAlias = tuple[
    _DriveChild[GraphValueT],
    _AbortChild,
    _ReleaseChild,
]
_ChildCall: TypeAlias = tuple[
    tuple[int, ...],
    ParentGraphActivation,
    _ChildPhase[GraphValueT],
    _ChildHandle[GraphValueT] | None,
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
        evidence_publisher: _EvidencePublisher[GraphValueT] | None = None,
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
        if evidence_publisher is None:
            evidence_publisher, _evidence_reader = _evidence_adapter((), ScopedFrameIndex())
        self._publish_evidence = evidence_publisher
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
    ) -> GraphRunState:
        commit_task = asyncio.create_task(
            commit_transition(self._scope_run, self._state, command, result, self._commit)
        )
        confirmed, cancellation = await wait_for_owner_task(commit_task)
        self._state = confirmed
        if cancellation is not None:
            raise cancellation
        return confirmed

    async def _fence(self, execution_token: GraphExecutionToken) -> None:
        await self._transition(FenceGraphExecution(self._state.revision, execution_token))

    def _install_terminal(
        self,
        index: int,
        boundary: ConfirmedChildBoundary[GraphValueT] | None,
    ) -> None:
        _position, parent, phase, handle = self._children[index]
        if not isinstance(phase, CompletedChild):
            if boundary is not None:
                raise ResultCollectionError("aborted child cannot provide a completed output boundary")
            return
        if boundary is None or boundary.frame != phase.output:
            raise ResultCollectionError("completed child did not provide its exact output boundary")
        child_graph = self._graph.nested_graphs[parent.node_id]
        availability = boundary.coordinate
        expected_scope_run = child_scope_run_for_activation(self._scope_run, parent)
        if (
            availability.child_scope_run != expected_scope_run
            or availability.descriptor != child_graph.graph_output_descriptor.identity
        ):
            raise SnapshotMismatchError("completed child boundary does not match its parent definition")
        if self._frames.has_child_boundary(availability):
            existing = self._frames.lookup(availability)
            if existing.frame != phase.output:
                raise SnapshotMismatchError("completed child output does not match its confirmed boundary")
            return
        self._frames = self._frames.add_child_boundary(ConfirmedChildBoundary(availability, phase.output))
        self._replace_child(index, phase, handle)

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
        commit_task = asyncio.create_task(commit_transition(coordinate, None, command, None, child_commit))
        child_state, cancellation = await wait_for_owner_task(commit_task)
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
                self._publish_evidence,
            )
            child.install_graph_input(child_input)
            handle = _opaque_handle(child, parent)
            self._children.append((position, parent, ActiveChild(parent), handle))
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
            with suppress(BaseException):
                await wait_for_owner_task(cleanup_task)
            raise
        if cancellation is not None:
            raise cancellation

    async def _drive_child(self, index: int) -> None:
        _position, _parent, phase, handle = self._children[index]
        if handle is None or not isinstance(phase, ActiveChild):
            return
        disposition, terminal, boundary = await handle[0]()
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
            binding for binding in bindings if binding.parent_activation.scope_run.scope == self._scope_run.scope
        )
        direct_activations = tuple(binding.parent_activation for binding in direct_candidates)
        direct_coordinates = tuple(binding.coordinate for binding in direct_candidates)
        if len(direct_activations) != len(set(direct_activations)) or len(direct_coordinates) != len(
            set(direct_coordinates)
        ):
            raise SnapshotMismatchError("continuation repeats one direct child activation")
        current_pending = frozenset(
            node_id for node_id in pending_node_ids(self._state.frontier) if node_id in self._graph.nested_graphs
        )
        admitted: list[ChildStateBinding] = []
        for binding in direct_candidates:
            activation = binding.parent_activation
            if activation.scope_run != self._scope_run:
                raise SnapshotMismatchError("continuation child binding belongs to a foreign parent run")
            if activation.node_id not in self._graph.nested_graphs:
                raise SnapshotMismatchError("continuation child binding has no parent nested definition")
            child_graph = self._graph.nested_graphs[activation.node_id]
            parent = ParentGraphActivation(
                self._state.run_id,
                activation.superstep,
                activation.node_id,
            )
            expected_coordinate = child_scope_run_for_activation(self._scope_run, parent)
            if (
                binding.coordinate != expected_coordinate
                or binding.state.run_id != binding.coordinate.graph_run_id
                or binding.state.parent != parent
            ):
                raise SnapshotMismatchError("continuation child binding has inconsistent activation coordinates")
            if activation.superstep > self._state.superstep:
                raise SnapshotMismatchError("continuation child binding is from a future parent frontier")
            current_activation = (
                self._state.status is GraphRunStatus.RUNNING
                and activation.superstep == self._state.superstep
                and activation.node_id in current_pending
            )
            if binding.state.status is GraphRunStatus.RUNNING and not current_activation:
                raise SnapshotMismatchError("running child binding is not one current pending nested activation")
            if binding.state.status is not GraphRunStatus.RUNNING:
                try:
                    GraphExecutor(child_graph).validate_state(binding.state)
                except GraphStateTransitionError as error:
                    raise SnapshotMismatchError("continuation child binding has invalid graph state") from error
            if current_activation:
                admitted.append(binding)
        direct = tuple(
            sorted(
                admitted,
                key=lambda binding: tuple(self._graph.nodes).index(binding.parent_activation.node_id),
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
                        self._publish_evidence,
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
                    with suppress(BaseException):
                        await wait_for_owner_task(cleanup_task)
                    raise
                status = frontier_status(binding.state.frontier)
                phase: _ChildPhase[GraphValueT] = (
                    AwaitingResume((), ()) if status is GraphFrontierStatus.AWAITING_RESUME else ActiveChild(parent)
                )
                self._children.append((position, parent, phase, handle))
                continue
            if binding.state.status is GraphRunStatus.COMPLETED:
                availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
                    binding.coordinate,
                    child_graph.graph_output_descriptor.identity,
                )
                phase = CompletedChild(parent, self._frames.lookup(availability).frame)
            else:
                abort = cast(GraphAbort, binding.state.abort)
                phase = AbortedChild(parent, abort.reason)
            self._children.append((position, parent, phase, None))
        self._children.sort(key=lambda call: call[0])

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
        disposition = await current.drive_quantum()
        if isinstance(disposition, AwaitingResume):
            current.handoff_evidence()
            handed_off = True
            return disposition, None, None
        terminal = current.terminal_projection(parent)
        boundary = current.terminal_boundary(terminal)
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


async def admit_root(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    child_states: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
    executors: tuple[tuple[ScopeRunCoordinate, GraphExecutor[GraphValueT]], ...],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> OwnerHandoff[GraphValueT]:
    scope_run = ScopeRunCoordinate((), state.run_id)
    evidence_publisher, evidence_reader = _evidence_adapter(child_states, frames)
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
            evidence_publisher,
        )
        await root.admit_existing_children(child_states, frames, executors)
        return root, evidence_reader
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

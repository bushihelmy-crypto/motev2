"""Owner-local graph-family driving, handoff, and result projection."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Generic, TypeAlias, TypeVar, cast, final

from mote_kernel.execution.cancellation import wait_for_owner_task
from mote_kernel.execution.commit import (
    GraphCommit,
    apply_commit_writes,
    commit_transition,
    confirm_transition,
    prepare_transition,
    scoped_commit,
)
from mote_kernel.execution.engine.admission import admit_child_graph_input, project_graph_outputs
from mote_kernel.execution.engine.resume_input import materialize_node_input
from mote_kernel.execution.engine.session import GraphExecutionSession, consume_node_origin_cancellation
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import ExecutableFrontier
from mote_kernel.execution.errors import FrameInstallationInvariantError, ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import GraphInputFrame, _public_values
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ScopeRunCoordinate,
    StableActivation,
    child_scope_run_for_activation,
    root_scope_run,
    stable_activation,
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
    SUPERSEDED_CHILD_ABORT_REASON,
    AbortedChild,
    AbortedGraph,
    ActiveChild,
    AwaitingResume,
    ChildProjection,
    CompletedChild,
    CompletedGraph,
    FailedChild,
    FailedGraph,
    GraphAbortView,
    GraphBoundary,
    GraphFailureView,
    GraphInterruptView,
    GraphResult,
    MissingChild,
    ReadyToResolve,
    TaskResult,
    WaitingForChildren,
    _aborted_result,
    _awaiting_result,
    _completed_result,
    _failed_result,
    _partial_commit_error,
)
from mote_kernel.execution.run_context import (
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedChildBoundary,
    ScopedFrameIndex,
    ScopedStateBinding,
    _CompiledFamilyIdentity,
    _make_continuation,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    FailedGraphNode,
    FenceGraphExecution,
    GraphAbort,
    GraphAbortReason,
    GraphActivationIdentity,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphRunCommand,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    graph_interrupt_id,
    pending_node_ids,
)

GraphValueT = TypeVar("GraphValueT")


_ChildTerminal: TypeAlias = CompletedChild[GraphValueT] | FailedChild | AbortedChild
_ChildPhase: TypeAlias = ActiveChild | AwaitingResume | _ChildTerminal[GraphValueT]
_EvidenceReader: TypeAlias = Callable[
    [],
    tuple[tuple[ScopedStateBinding, ...], ScopedFrameIndex[GraphValueT]],
]
_EvidencePublisher: TypeAlias = Callable[[ScopedStateBinding, ScopedFrameIndex[GraphValueT]], None]


@final
class _ChildCall(Generic[GraphValueT]):
    """The explicit lifecycle owner for one nested graph activation."""

    __slots__ = ("_owner", "parent", "phase", "position")

    def __init__(
        self,
        position: tuple[int, ...],
        parent: GraphActivationIdentity,
        phase: _ChildPhase[GraphValueT],
        owner: _GraphRun[GraphValueT] | None,
    ) -> None:
        if isinstance(phase, AwaitingResume):
            raise ResultCollectionError("a restored child call must already be terminal")
        if owner is None and not isinstance(phase, CompletedChild | FailedChild | AbortedChild):
            raise ResultCollectionError("an active child call requires one live child owner")
        if owner is not None and not isinstance(phase, ActiveChild):
            raise ResultCollectionError("a live child owner must enter through the active phase")
        if phase.parent != parent:
            raise ResultCollectionError("child call phase does not match its parent activation")
        self.position = position
        self.parent = parent
        self.phase = phase
        self._owner = owner

    @property
    def live(self) -> bool:
        return self._owner is not None

    def _require_owner(self) -> _GraphRun[GraphValueT]:
        if self._owner is None:
            raise ResultCollectionError("child call was already released")
        return self._owner

    async def drive(self) -> ConfirmedChildBoundary[GraphValueT] | asyncio.CancelledError | None:
        current = self._require_owner()
        if not isinstance(self.phase, ActiveChild):
            raise ResultCollectionError("only an active child call can be driven")
        try:
            disposition = await current.drive_quantum()
        except asyncio.CancelledError as error:
            if current.consume_commit_origin_cancellation(error):
                return error
            raise
        if isinstance(disposition, AwaitingResume):
            current.handoff_evidence()
            self.phase = disposition
            return None
        terminal = current.terminal_projection(self.parent)
        boundary = current.terminal_boundary(self.parent, terminal)
        if isinstance(disposition, CompletedGraph) and not isinstance(terminal, CompletedChild):
            raise ResultCollectionError("completed child returned a non-completed terminal projection")
        if isinstance(disposition, FailedGraph) and not isinstance(terminal, FailedChild):
            raise ResultCollectionError("failed child returned a non-failed terminal projection")
        if isinstance(disposition, AbortedGraph) and not isinstance(terminal, AbortedChild):
            raise ResultCollectionError("aborted child returned a non-aborted terminal projection")
        current.handoff_evidence()
        self.phase = terminal
        return boundary

    async def abort(self, reason: GraphAbortReason) -> None:
        current = self._owner
        if current is None:
            return
        try:
            await current.abort(reason)
        finally:
            if current.state.status is not GraphRunStatus.RUNNING:
                current.handoff_evidence()
                self.phase = current.terminal_projection(self.parent)

    async def fence(self) -> None:
        current = self._owner
        if current is None:
            return
        try:
            await current.fence_after_worker_failure()
        finally:
            if current.state.execution is None:
                current.handoff_evidence()

    async def release(self) -> None:
        current = self._owner
        if current is None:
            return
        await current.release()
        self._owner = None


_ChildConstructor: TypeAlias = Callable[
    [
        GraphActivationIdentity,
        CompiledGraph[GraphValueT],
        GraphInputFrame[GraphValueT],
        tuple[int, ...],
    ],
    Coroutine[None, None, _ChildCall[GraphValueT]],
]


def _child_failure_reason(state: GraphRunState) -> str:
    failures = tuple(
        (node.node_id, str(node.settlement.failure))
        for node in state.frontier.nodes
        if isinstance(node.settlement, FailedGraphNode)
    )
    if not failures:
        raise ResultCollectionError("failed child state contains no failed frontier node")
    if len(failures) == 1:
        return failures[0][1]
    return "nested graph failed: " + "; ".join(f"{node_id}: {failure}" for node_id, failure in failures)


async def _cleanup_unhanded_child(
    call: _ChildCall[GraphValueT],
    reason: GraphAbortReason,
    *,
    abort: bool,
) -> None:
    async def cleanup() -> None:
        if abort:
            with suppress(BaseException):
                await call.abort(reason)
        with suppress(BaseException):
            await call.release()

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


def _frames_for_owner(
    frames: ScopedFrameIndex[GraphValueT],
    bindings: tuple[ScopedStateBinding, ...],
    owner: ScopeRunCoordinate,
) -> ScopedFrameIndex[GraphValueT]:
    # Preserve the first binding if a malformed direct caller repeats a key.
    bindings_by_scope = {binding.scope_run: binding for binding in reversed(bindings)}
    child_boundaries: list[ConfirmedChildBoundary[GraphValueT]] = []
    for record in frames.child_boundaries:
        binding = bindings_by_scope.get(record.coordinate.child_scope_run)
        if binding is None:
            raise SnapshotMismatchError(f"continuation has no child binding at {record.coordinate.child_scope_run!r}")
        parent_activation = binding.parent_activation
        if parent_activation is not None and parent_activation.scope_run == owner:
            child_boundaries.append(record)
    return ScopedFrameIndex(
        graph_inputs=tuple(record for record in frames.graph_inputs if record.coordinate.scope_run == owner),
        publications=tuple(record for record in frames.publications if record.coordinate.activation.scope_run == owner),
        resume_inputs=tuple(
            record for record in frames.resume_inputs if record.coordinate.activation.scope_run == owner
        ),
        child_boundaries=tuple(child_boundaries),
    )


def _evidence_adapter(
    bindings: tuple[ScopedStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
) -> tuple[_EvidencePublisher[GraphValueT], _EvidenceReader[GraphValueT]]:
    # The coordinate is the evidence identity; lineage admission guarantees
    # uniqueness and the adapter owns the current binding/frame value.
    entries: dict[ScopeRunCoordinate, tuple[ScopedStateBinding, ScopedFrameIndex[GraphValueT]]] = {
        binding.scope_run: (
            binding,
            _frames_for_owner(frames, bindings, binding.scope_run),
        )
        for binding in reversed(bindings)
    }

    def publish(binding: ScopedStateBinding, owner_frames: ScopedFrameIndex[GraphValueT]) -> None:
        _ = binding.parent_activation
        entries[binding.scope_run] = (binding, owner_frames)

    def read() -> tuple[tuple[ScopedStateBinding, ...], ScopedFrameIndex[GraphValueT]]:
        canonical = tuple(entries[coordinate] for coordinate in sorted(entries))
        return (
            tuple(binding for binding, _frames in canonical),
            _merge_frames(tuple(owner_frames for _binding, owner_frames in canonical)),
        )

    return publish, read


class _GraphRun(Generic[GraphValueT]):
    """The sole live owner of one scoped graph run."""

    __slots__ = (
        "_child_constructor",
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
        limits: ExecutionLimits,
        commit: GraphCommit[GraphValueT],
        child_constructor: _ChildConstructor[GraphValueT],
        position: tuple[int, ...],
        parent_activation: StableActivation | None,
        evidence_publisher: _EvidencePublisher[GraphValueT],
    ) -> None:
        require_scoped_snapshot_matches_graph(graph, state, scope_run)
        self._graph = graph
        self._scope_run = scope_run
        self._state = state
        self._frames = frames
        self._executor = GraphExecutor(graph)
        self._limits = limits
        self._commit = commit
        self._child_constructor = child_constructor
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

    def _child_call(self, parent: GraphActivationIdentity) -> _ChildCall[GraphValueT] | None:
        return next((call for call in self._children if call.parent == parent), None)

    def child_position(self, parent: GraphActivationIdentity) -> tuple[int, ...]:
        if self._child_call(parent) is not None:
            raise ResultCollectionError("one parent activation cannot admit more than one child call")
        node_ids = tuple(self._graph.nodes)
        try:
            ordinal = node_ids.index(parent.node_id)
        except ValueError as error:
            raise ResultCollectionError("child activation is not part of the parent definition") from error
        generation = sum(1 for call in self._children if call.parent.node_id == parent.node_id)
        return (*self._position, ordinal, generation)

    def accept_child_call(self, call: _ChildCall[GraphValueT]) -> None:
        if call.position != self.child_position(call.parent):
            raise ResultCollectionError("child call position does not match its parent activation")
        self._children.append(call)
        self._children.sort(key=lambda candidate: candidate.position)

    def _child_projections(self) -> tuple[ChildProjection[GraphValueT], ...]:
        projections: list[ChildProjection[GraphValueT]] = []
        for node_id in pending_node_ids(self._state.frontier):
            if node_id not in self._graph.nested_graphs:
                continue
            parent = GraphActivationIdentity(self._state.run_id, self._state.superstep, node_id)
            call = self._child_call(parent)
            if call is None:
                projections.append(MissingChild(parent))
                continue
            phase = call.phase
            if isinstance(phase, AwaitingResume | ActiveChild):
                projections.append(ActiveChild(parent))
            else:
                projections.append(phase)
        return tuple(projections)

    async def _transition(
        self,
        command: GraphRunCommand,
        result: TaskResult[GraphValueT] | None = None,
        *,
        admitted_successor: GraphRunState | None = None,
        confirmed_frames: ScopedFrameIndex[GraphValueT] | None = None,
        handoff_evidence: bool = False,
    ) -> GraphRunState:
        transition = prepare_transition(
            self._scope_run,
            self._state,
            command,
            result,
            graph=self._graph,
            admitted_successor=admitted_successor,
        )
        if confirmed_frames is not None and (transition.writes.graph_inputs or transition.writes.publications):
            raise FrameInstallationInvariantError("a transition cannot stage frames and an admitted frame snapshot")
        commit_task = asyncio.create_task(confirm_transition(transition, self._commit))
        confirmed, cancellation = await wait_for_owner_task(
            commit_task,
            self._mark_commit_origin_cancellation,
        )
        self._state = confirmed
        self._frames = (
            apply_commit_writes(self._frames, transition.writes) if confirmed_frames is None else confirmed_frames
        )
        if handoff_evidence and self._parent_activation is not None:
            self.handoff_evidence()
        if cancellation is not None:
            raise cancellation
        return self._state

    async def apply_admission_fence(self, command: FenceGraphExecution) -> None:
        await self._transition(command, handoff_evidence=True)

    async def apply_admission_resume(self, planned: PlannedResume[GraphValueT]) -> None:
        confirmed_frames = project_resume_frames(self._frames, planned)
        await self._transition(
            planned.prepared.command,
            admitted_successor=planned.successor,
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
        call: _ChildCall[GraphValueT],
        boundary: ConfirmedChildBoundary[GraphValueT] | None,
    ) -> None:
        phase = call.phase
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
            self._child_constructor(
                parent,
                child_graph,
                child_input,
                position,
            )
        )
        call, cancellation = await wait_for_owner_task(
            construction,
            self._mark_commit_origin_cancellation,
        )
        try:
            self.accept_child_call(call)
        except BaseException:
            await _cleanup_unhanded_child(
                call,
                GraphAbortReason("nested graph owner handoff failed"),
                abort=True,
            )
            raise
        if cancellation is not None:
            raise cancellation

    async def _drive_child(self, call: _ChildCall[GraphValueT]) -> None:
        if not isinstance(call.phase, ActiveChild):
            return
        child_result = await call.drive()
        if isinstance(child_result, asyncio.CancelledError):
            self._mark_commit_origin_cancellation(child_result)
            raise child_result
        if isinstance(call.phase, CompletedChild | FailedChild | AbortedChild):
            self._install_terminal(call, child_result)

    async def _abort_awaiting_children_after_failure(self) -> bool:
        if not any(isinstance(node.settlement, FailedGraphNode) for node in self._state.frontier.nodes):
            return False
        reason = SUPERSEDED_CHILD_ABORT_REASON
        aborted = False
        for call in tuple(self._children):
            if not isinstance(call.phase, AwaitingResume):
                continue
            if not call.live:
                raise ResultCollectionError("awaiting child has no live owner")
            await call.abort(reason)
            aborted = True
        return aborted

    async def fence_after_worker_failure(self) -> None:
        """Stop this family after a sibling worker failed normally.

        The fan-in owner has already waited for every worker to stop before
        entering this method.  Each still-owned child is fenced through the
        same child owner, then this scope's session and execution lease are
        fenced.  No lifecycle command is synthesized: the scopes remain
        ``RUNNING`` so the confirmed snapshot can be recovered later.
        """

        errors: list[BaseException] = []
        for call in tuple(self._children):
            if not call.live or isinstance(call.phase, CompletedChild | FailedChild | AbortedChild):
                continue
            try:
                await call.fence()
            except BaseException as error:
                errors.append(error)
        session = self._session
        if session is not None:
            try:
                await session.aclose()
            except BaseException as error:
                errors.append(error)
            finally:
                self._session = None
        if self._state.status is GraphRunStatus.RUNNING and self._state.execution is not None:
            try:
                await self._fence(self._state.execution.token)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    async def _retire_child(self, result: TaskResult[GraphValueT]) -> None:
        parent = GraphActivationIdentity(result.task.run_id, result.task.superstep, result.task.node_id)
        call = self._child_call(parent)
        if call is None:
            raise ResultCollectionError("settled nested node has no admitted child call")
        if not isinstance(call.phase, CompletedChild | FailedChild | AbortedChild):
            raise ResultCollectionError("nested node settlement requires one unretired terminal child")
        await call.release()

    async def _consume_session(
        self,
        session: GraphExecutionSession[GraphValueT],
        execution_token: GraphExecutionToken,
    ) -> None:
        async with session:
            while True:
                try:
                    completed = await session.next(self._state)
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
                result = completed.result
                await self._transition(completed.command, result)
                task = result.task
                if task.node_id in self._graph.nested_graphs:
                    await self._retire_child(result)

    async def _drive_workers(
        self,
        workers_to_start: tuple[tuple[str, Coroutine[None, None, None]], ...],
    ) -> None:
        """Drive owner-local work while retaining every task handle.

        A child is an independent graph owner, but it is not an independent
        invocation.  The family owner keeps every worker here, so ordinary
        sessions and child-only frontiers share the same cancellation and
        error boundary without creating another scheduler.
        """

        if not workers_to_start:
            return
        workers_list: list[asyncio.Task[None]] = []
        try:
            for label, worker in workers_to_start:
                workers_list.append(
                    asyncio.create_task(
                        worker,
                        name=f"mote-graph-family:{self._scope_run.graph_run_id}:{label}",
                    )
                )
        except BaseException:
            for _label, worker in workers_to_start[len(workers_list) :]:
                worker.close()
            for worker in workers_list:
                worker.cancel()
            await asyncio.gather(*workers_list, return_exceptions=True)
            raise
        workers = tuple(workers_list)
        order = {worker: position for position, worker in enumerate(workers)}
        pending: set[asyncio.Task[None]] = set(workers)

        async def cancel_workers() -> None:
            live = tuple(worker for worker in workers if not worker.done())
            for worker in live:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        worker_failures: tuple[tuple[int, BaseException], ...] = ()
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                failures: list[tuple[int, BaseException]] = []
                for worker in sorted(done, key=order.__getitem__):
                    try:
                        worker.result()
                    except BaseException as error:
                        failures.append((order[worker], error))
                if failures:
                    worker_failures = tuple(failures)
                    break
        except BaseException:
            cleanup_task = asyncio.create_task(cancel_workers())
            with suppress(BaseException):
                await wait_for_owner_task(cleanup_task)
            raise

        if not worker_failures:
            return

        primary = min(worker_failures, key=lambda item: item[0])[1]
        cleanup_error: BaseException | None = None
        cancellation_cleanup = asyncio.create_task(cancel_workers())
        try:
            await wait_for_owner_task(cancellation_cleanup)
        except BaseException as error:
            cleanup_error = error

        # Commit- and node-origin cancellation deliberately keep their
        # authoritative lease for caller handling or recovery.  Every other
        # worker failure is a fan-in stop: fence the complete family after all
        # Python tasks have settled.
        family_failure = not (primary is self._commit_origin_cancellation or primary is self._node_origin_cancellation)
        if family_failure:
            fence_cleanup = asyncio.create_task(self.fence_after_worker_failure())
            try:
                await wait_for_owner_task(fence_cleanup)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error

        if cleanup_error is not None:
            raise primary from cleanup_error
        raise primary

    async def _execute_frontier(
        self,
        prepared: ExecutableFrontier[GraphValueT],
    ) -> None:
        if prepared.children is not None:
            for missing in prepared.children.missing:
                await self._start_child(missing)
        claimed = await self._transition(prepared.claim.command)
        execution = cast(GraphExecutionLease, claimed.execution)
        try:
            session = self._executor.issue_session(prepared.claim, claimed)
        except Exception:
            await self._fence(execution.token)
            raise
        self._session = session
        try:
            child_calls = tuple(
                call
                for call in self._children
                if (
                    isinstance(call.phase, ActiveChild)
                    and call.parent.run_id == self._state.run_id
                    and call.parent.superstep == self._state.superstep
                )
            )
            workers = (
                ("parent", self._consume_session(session, execution.token)),
                *((f"child:{index}", self._drive_child(call)) for index, call in enumerate(child_calls)),
            )
            await self._drive_workers(workers)
        finally:
            self._session = None
        if self._state.execution is not None:
            await self._fence(execution.token)

    async def drive_quantum(self) -> GraphBoundary:
        while True:
            disposition = self._executor.prepare(
                StepRequest(
                    self._state,
                    self._scope_run,
                    self._frames,
                    self._child_projections(),
                    self._limits,
                )
            )
            if isinstance(disposition, ReadyToResolve):
                await self._transition(disposition.command)
                continue
            if isinstance(disposition, ExecutableFrontier):
                await self._execute_frontier(disposition)
                continue
            if isinstance(disposition, WaitingForChildren):
                if disposition.missing:
                    for missing in disposition.missing:
                        await self._start_child(missing)
                    continue
                child_workers: list[tuple[str, Coroutine[None, None, None]]] = []
                for active in disposition.active:
                    call = self._child_call(active.parent)
                    if call is None:
                        raise ResultCollectionError("active child projection has no admitted child call")
                    if isinstance(call.phase, ActiveChild):
                        child_workers.append((f"child:{len(child_workers)}", self._drive_child(call)))
                if child_workers:
                    await self._drive_workers(tuple(child_workers))
                    continue
                if await self._abort_awaiting_children_after_failure():
                    continue
                return AwaitingResume(())
            return disposition

    def handoff_evidence(self) -> None:
        if self._parent_activation is None:
            raise SnapshotMismatchError("root graph evidence cannot be handed off as a child binding")
        binding = ScopedStateBinding(self._scope_run, self._state)
        self._publish_evidence(binding, self._frames)

    def freeze_root_evidence(
        self,
        evidence_reader: _EvidenceReader[GraphValueT],
    ) -> tuple[GraphRunState, tuple[ScopedStateBinding, ...], ScopedFrameIndex[GraphValueT]]:
        if self._parent_activation is not None:
            raise SnapshotMismatchError("child graph evidence cannot be exported as the root")
        if any(isinstance(call.phase, ActiveChild) for call in self._children):
            raise SnapshotMismatchError("active child call has no handed-off export evidence")
        descendants, frames = evidence_reader()
        return self._state, descendants, _merge_frames((self._frames, frames))

    def consume_node_origin_cancellation(self, error: asyncio.CancelledError) -> bool:
        if self._node_origin_cancellation is not error:
            return False
        self._node_origin_cancellation = None
        return True

    def terminal_projection(self, parent: GraphActivationIdentity) -> _ChildTerminal[GraphValueT]:
        if self._state.status is GraphRunStatus.COMPLETED:
            return CompletedChild(
                parent,
                project_graph_outputs(
                    self._graph,
                    self._scope_run,
                    self._state.superstep,
                    self._frames,
                ),
                None if self._state.completion_route is None else str(self._state.completion_route),
            )
        if self._state.status is GraphRunStatus.FAILED:
            return FailedChild(parent, _child_failure_reason(self._state))
        if self._state.status is GraphRunStatus.ABORTED and self._state.abort is not None:
            return AbortedChild(parent, self._state.abort.reason)
        raise ResultCollectionError("child evidence is not terminal")

    def terminal_boundary(
        self,
        parent: GraphActivationIdentity,
        terminal: _ChildTerminal[GraphValueT],
    ) -> ConfirmedChildBoundary[GraphValueT] | None:
        activation = self._parent_activation
        expected_parent = (
            None
            if activation is None
            else GraphActivationIdentity(
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
        for call in self._children:
            if not call.live or isinstance(call.phase, CompletedChild | FailedChild | AbortedChild):
                continue
            try:
                await call.abort(reason)
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
        for call in tuple(self._children):
            if not call.live:
                continue
            try:
                await call.release()
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


def _make_child_constructor(
    owner_graph: CompiledGraph[GraphValueT],
    owner_scope_run: ScopeRunCoordinate,
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
    evidence_publisher: _EvidencePublisher[GraphValueT],
) -> _ChildConstructor[GraphValueT]:
    async def construct(
        parent: GraphActivationIdentity,
        child_graph: CompiledGraph[GraphValueT],
        child_input: GraphInputFrame[GraphValueT],
        position: tuple[int, ...],
    ) -> _ChildCall[GraphValueT]:
        expected_graph = owner_graph.nested_graphs.get(parent.node_id)
        if expected_graph is None:
            raise SnapshotMismatchError("child construction references a non-nested parent activation")
        if child_graph is not expected_graph:
            raise SnapshotMismatchError("child construction does not match its parent topology")
        coordinate = child_scope_run_for_activation(owner_scope_run, parent)
        activation = stable_activation(owner_scope_run, parent)
        child_commit = scoped_commit(coordinate, commit)
        command = project_start_graph_command(
            child_graph,
            coordinate.graph_run_id,
            parent,
            child_input.activation_config.config_cursor if child_input.activation_config is not None else None,
        )
        transition = prepare_transition(
            coordinate,
            None,
            command,
            None,
            graph=child_graph,
            graph_input=child_input,
        )
        child_state = await confirm_transition(transition, child_commit)
        try:
            staged_frames = apply_commit_writes(ScopedFrameIndex(), transition.writes)
            child = _GraphRun(
                child_graph,
                coordinate,
                child_state,
                staged_frames,
                limits,
                child_commit,
                _make_child_constructor(child_graph, coordinate, limits, commit, evidence_publisher),
                position,
                activation,
                evidence_publisher,
            )
        except BaseException:

            async def cleanup_candidate() -> None:
                reason = GraphAbortReason("nested graph owner construction failed")
                await commit_transition(
                    coordinate,
                    child_state,
                    AbortGraphRun(child_state.revision, reason),
                    None,
                    child_commit,
                    graph=child_graph,
                )

            cleanup_task = asyncio.create_task(cleanup_candidate())
            with suppress(BaseException):
                await wait_for_owner_task(cleanup_task)
            raise
        return _ChildCall(position, parent, ActiveChild(parent), child)

    return construct


async def admit_continued_root(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    child_states: tuple[ScopedStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
    fences: tuple[PlannedFence, ...],
    resumes: tuple[PlannedResume[GraphValueT], ...],
    family_identity: _CompiledFamilyIdentity,
    *,
    recovered: bool,
) -> OwnerHandoff[GraphValueT]:
    scope_run = root_scope_run(state.run_id)
    fences_by_scope = {candidate.scope_run: candidate for candidate in fences}
    resumes_by_scope = {candidate.scope_run: candidate for candidate in resumes}
    evidence_publisher, evidence_reader = _evidence_adapter(child_states, frames)
    root_commit = scoped_commit(scope_run, commit)
    root: _GraphRun[GraphValueT] | None = None
    confirmed_prefix = False
    transition_attempted = False
    failed_scope: tuple[str, ...] | None = None

    def build_owner(
        owner_graph: CompiledGraph[GraphValueT],
        owner_scope_run: ScopeRunCoordinate,
        owner_state: GraphRunState,
        owner_commit: GraphCommit[GraphValueT],
        position: tuple[int, ...],
        parent_activation: StableActivation | None,
    ) -> _GraphRun[GraphValueT]:
        owner_frames = _frames_for_owner(frames, child_states, owner_scope_run)
        return _GraphRun(
            owner_graph,
            owner_scope_run,
            owner_state,
            owner_frames,
            limits,
            owner_commit,
            _make_child_constructor(owner_graph, owner_scope_run, limits, commit, evidence_publisher),
            position,
            parent_activation,
            evidence_publisher,
        )

    async def cleanup_owner(
        owner: _GraphRun[GraphValueT] | None,
        owner_graph: CompiledGraph[GraphValueT],
        owner_scope_run: ScopeRunCoordinate,
        owner_state: GraphRunState,
        owner_commit: GraphCommit[GraphValueT],
    ) -> None:
        reason = GraphAbortReason("continued graph owner construction failed")
        if owner is None:
            if not transition_attempted and owner_state.status is GraphRunStatus.RUNNING:
                with suppress(BaseException):
                    await commit_transition(
                        owner_scope_run,
                        owner_state,
                        AbortGraphRun(owner_state.revision, reason),
                        None,
                        owner_commit,
                        graph=owner_graph,
                    )
            return
        if not transition_attempted:
            with suppress(BaseException):
                await owner.abort(reason)
        with suppress(BaseException):
            await owner.release()

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
        parent: GraphActivationIdentity,
        binding: ScopedStateBinding,
        child_graph: CompiledGraph[GraphValueT],
        position: tuple[int, ...],
        fence: PlannedFence | None,
        resume: PlannedResume[GraphValueT] | None,
    ) -> _ChildCall[GraphValueT]:
        nonlocal failed_scope
        child: _GraphRun[GraphValueT] | None = None
        child_commit = scoped_commit(binding.scope_run, commit)
        try:
            parent_activation = binding.parent_activation
            if parent_activation is None:
                raise SnapshotMismatchError("nested child binding is missing its parent activation")
            child = build_owner(
                child_graph,
                binding.scope_run,
                binding.state,
                child_commit,
                position,
                parent_activation,
            )
            await apply_admission(child, fence, resume, tuple(binding.scope_run.scope))
            await admit_children(child, child_graph, binding.scope_run, binding.state)
            return _ChildCall(position, parent, ActiveChild(parent), child)
        except BaseException:
            if failed_scope is None:
                failed_scope = tuple(binding.scope_run.scope)

            cleanup_task = asyncio.create_task(
                cleanup_owner(child, child_graph, binding.scope_run, binding.state, child_commit)
            )
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
        # lineage_states validates coordinate order; filtering preserves child node order for this owner.
        for binding in child_states:
            activation = binding.parent_activation
            if activation is None:
                raise SnapshotMismatchError("nested child binding is missing its parent activation")
            if activation.scope_run != owner_scope_run or not is_current_child_activation(owner_state, activation):
                continue
            parent = GraphActivationIdentity(
                owner_state.run_id,
                activation.superstep,
                activation.node_id,
            )
            child_graph = owner_graph.nested_graphs[activation.node_id]
            position = owner.child_position(parent)
            if binding.state.status is not GraphRunStatus.RUNNING:
                if binding.state.status is GraphRunStatus.COMPLETED:
                    availability: ChildBoundaryAvailabilityCoordinate[GraphValueT] = (
                        ChildBoundaryAvailabilityCoordinate(
                            binding.scope_run,
                            child_graph.graph_output_descriptor.identity,
                        )
                    )
                    phase: _ChildPhase[GraphValueT] = CompletedChild(
                        parent,
                        owner.frames.lookup(availability).frame,
                        None if binding.state.completion_route is None else str(binding.state.completion_route),
                    )
                elif binding.state.status is GraphRunStatus.FAILED:
                    phase = FailedChild(parent, _child_failure_reason(binding.state))
                else:
                    abort = cast(GraphAbort, binding.state.abort)
                    phase = AbortedChild(parent, abort.reason)
                owner.accept_child_call(_ChildCall(position, parent, phase, None))
                continue

            fence = fences_by_scope.get(binding.scope_run)
            resume = resumes_by_scope.get(binding.scope_run)
            call = await construct_child(parent, binding, child_graph, position, fence, resume)
            try:
                owner.accept_child_call(call)
            except BaseException:
                failed_scope = tuple(binding.scope_run.scope)
                await _cleanup_unhanded_child(
                    call,
                    GraphAbortReason("continued graph owner handoff failed"),
                    abort=not transition_attempted,
                )
                raise

    try:
        root = build_owner(
            graph,
            scope_run,
            state,
            root_commit,
            (),
            None,
        )
        root_fence = fences_by_scope.get(scope_run)
        root_resume = resumes_by_scope.get(scope_run)
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

        cleanup_task = asyncio.create_task(cleanup_owner(root, graph, scope_run, state, root_commit))
        with suppress(BaseException):
            await wait_for_owner_task(cleanup_task)
        raise


async def fresh_root(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    input_frame: GraphInputFrame[GraphValueT],
    limits: ExecutionLimits,
    commit: GraphCommit[GraphValueT] | None,
) -> OwnerHandoff[GraphValueT]:
    evidence_publisher, evidence_reader = _evidence_adapter((), ScopedFrameIndex())
    child_constructor = _make_child_constructor(graph, scope_run, limits, commit, evidence_publisher)
    root_commit = scoped_commit(scope_run, commit)
    command = project_start_graph_command(
        graph,
        scope_run.graph_run_id,
        config_cursor=(
            input_frame.activation_config.config_cursor if input_frame.activation_config is not None else None
        ),
    )
    transition = prepare_transition(
        scope_run,
        None,
        command,
        None,
        graph=graph,
        graph_input=input_frame,
    )
    state = await confirm_transition(transition, root_commit)
    try:
        root = _GraphRun(
            graph,
            scope_run,
            state,
            apply_commit_writes(ScopedFrameIndex(), transition.writes),
            limits,
            root_commit,
            child_constructor,
            (),
            None,
            evidence_publisher,
        )
        return root, evidence_reader
    except BaseException:

        async def cleanup_root() -> None:
            with suppress(BaseException):
                await commit_transition(
                    scope_run,
                    state,
                    AbortGraphRun(
                        state.revision,
                        GraphAbortReason("root graph owner construction failed"),
                    ),
                    None,
                    root_commit,
                    graph=graph,
                )

        cleanup_task = asyncio.create_task(cleanup_root())
        with suppress(BaseException):
            await wait_for_owner_task(cleanup_task)
        raise


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
    if type(disposition) not in (CompletedGraph, FailedGraph, AbortedGraph, AwaitingResume):
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
        *((tuple(binding.scope_run.scope), binding.state) for binding in child_states),
    )
    failures, interrupts = _project_result_views(scoped_states)
    if isinstance(disposition, FailedGraph):
        return _failed_result(state, continuation, failures, interrupts)
    return _awaiting_result(state, continuation, interrupts)


__all__: list[str] = []

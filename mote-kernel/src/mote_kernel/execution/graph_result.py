"""Sealed graph-family results and exact-commit continuation handoffs."""

import asyncio
from dataclasses import InitVar, dataclass
from typing import Generic, Never, SupportsIndex, TypeAlias, TypeVar, final

from mote_kernel.execution.commit import GraphCommit
from mote_kernel.execution.errors import ExecutionError, NodeExecutionContractError, SnapshotMismatchError
from mote_kernel.execution.graph.values import _GraphValues
from mote_kernel.execution.run_context import ScopedFrameIndex, ScopedRunEvidence
from mote_kernel.state.graph_state import GraphInterruptId, GraphRunState

GraphValueT = TypeVar("GraphValueT")
_PartialCommitCause: TypeAlias = Exception | asyncio.CancelledError


@dataclass(frozen=True, slots=True, eq=False)
class _CompiledFamilyIdentity:
    pass


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ContinuationSnapshot(Generic[GraphValueT]):
    """One immutable handoff, including its exact commit capability."""

    family_identity: _CompiledFamilyIdentity
    root_state: GraphRunState
    child_runs: tuple[ScopedRunEvidence, ...]
    frames: ScopedFrameIndex[GraphValueT]
    recovered: bool
    commit: GraphCommit[GraphValueT] | None


class _ContinuationSeal:
    __slots__ = ()


_CONTINUATION_SEAL = _ContinuationSeal()


@final
@dataclass(frozen=True, slots=True, kw_only=True, eq=False, repr=False)
class _GraphContinuation(Generic[GraphValueT]):
    _snapshot: ContinuationSnapshot[GraphValueT]
    _seal: InitVar[_ContinuationSeal]

    def __post_init__(self, _seal: _ContinuationSeal) -> None:
        if _seal is not _CONTINUATION_SEAL:
            raise SnapshotMismatchError("continuations can only be produced by a Graph result")

    def admit_snapshot(
        self,
        _seal: _ContinuationSeal,
        family_identity: _CompiledFamilyIdentity,
        state: GraphRunState,
        commit: GraphCommit[GraphValueT] | None,
    ) -> ContinuationSnapshot[GraphValueT]:
        if _seal is not _CONTINUATION_SEAL:
            raise SnapshotMismatchError("continuations can only be admitted by their Graph owner")
        snapshot = self._snapshot
        if snapshot.family_identity is not family_identity or snapshot.root_state != state:
            raise SnapshotMismatchError("state and continuation do not belong to the same compiled graph lineage")
        if commit is not None and commit is not snapshot.commit:
            raise SnapshotMismatchError("continuation cannot replace its commit capability")
        return snapshot

    def __copy__(self) -> Never:
        raise SnapshotMismatchError("continuations do not provide a copy contract")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> Never:
        raise SnapshotMismatchError("continuations do not provide a serialization contract")


def _admit_continuation(
    family_identity: _CompiledFamilyIdentity,
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
    commit: GraphCommit[GraphValueT] | None,
) -> ContinuationSnapshot[GraphValueT]:
    if type(continuation) is not _GraphContinuation:
        raise SnapshotMismatchError("continuations can only be admitted by their Graph owner")
    return continuation.admit_snapshot(_CONTINUATION_SEAL, family_identity, state, commit)


def _make_continuation(
    family_identity: _CompiledFamilyIdentity,
    root_state: GraphRunState,
    child_runs: tuple[ScopedRunEvidence, ...],
    frames: ScopedFrameIndex[GraphValueT],
    *,
    recovered: bool,
    commit: GraphCommit[GraphValueT] | None,
) -> _GraphContinuation[GraphValueT]:
    snapshot = ContinuationSnapshot(family_identity, root_state, child_runs, frames, recovered, commit)
    return _GraphContinuation(_snapshot=snapshot, _seal=_CONTINUATION_SEAL)


class _PartialCommitSeal:
    __slots__ = ()


_PARTIAL_COMMIT_SEAL = _PartialCommitSeal()


@final
class _PartialCommitError(ExecutionError, Generic[GraphValueT]):
    """Explicit handoff for an invocation that exactly confirmed only a prefix."""

    __slots__ = ("cause", "continuation", "failed_scope", "state")

    def __init__(
        self,
        *,
        state: GraphRunState,
        continuation: _GraphContinuation[GraphValueT],
        cause: _PartialCommitCause,
        failed_scope: tuple[str, ...],
        _seal: _PartialCommitSeal,
    ) -> None:
        if _seal is not _PARTIAL_COMMIT_SEAL:
            raise SnapshotMismatchError("partial commit errors can only be produced by their Graph owner")
        super().__init__(f"graph commit failed at scope {failed_scope!r} after an exact-confirmed prefix")
        self.state = state
        self.continuation = continuation
        self.cause = cause
        self.failed_scope = failed_scope


def _partial_commit_error(
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
    cause: _PartialCommitCause,
    failed_scope: tuple[str, ...],
) -> _PartialCommitError[GraphValueT]:
    return _PartialCommitError(
        state=state,
        continuation=continuation,
        cause=cause,
        failed_scope=failed_scope,
        _seal=_PARTIAL_COMMIT_SEAL,
    )


@dataclass(frozen=True, slots=True)
class GraphFailureView:
    scope: tuple[str, ...]
    node_id: str
    failure: str


@dataclass(frozen=True, slots=True)
class GraphInterruptView:
    scope: tuple[str, ...]
    node_id: str
    interrupt_id: GraphInterruptId
    request_payload: bytes


@dataclass(frozen=True, slots=True)
class GraphAbortView:
    scope: tuple[str, ...]
    reason: str


class _ResultSeal:
    __slots__ = ()


_RESULT_SEAL = _ResultSeal()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _CompletedGraphResult(Generic[GraphValueT]):
    state: GraphRunState
    continuation: _GraphContinuation[GraphValueT]
    outputs: _GraphValues[GraphValueT]
    _seal: InitVar[_ResultSeal]

    def __post_init__(self, _seal: _ResultSeal) -> None:
        if _seal is not _RESULT_SEAL:
            raise NodeExecutionContractError("completed results require the graph family driver")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _AbortedGraphResult(Generic[GraphValueT]):
    state: GraphRunState
    continuation: _GraphContinuation[GraphValueT]
    abort: GraphAbortView
    _seal: InitVar[_ResultSeal]

    def __post_init__(self, _seal: _ResultSeal) -> None:
        if _seal is not _RESULT_SEAL:
            raise NodeExecutionContractError("aborted results require the graph family driver")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _FailedGraphResult(Generic[GraphValueT]):
    state: GraphRunState
    continuation: _GraphContinuation[GraphValueT]
    failures: tuple[GraphFailureView, ...]
    interrupts: tuple[GraphInterruptView, ...]
    _seal: InitVar[_ResultSeal]

    def __post_init__(self, _seal: _ResultSeal) -> None:
        if _seal is not _RESULT_SEAL:
            raise NodeExecutionContractError("failed results require the graph family driver")
        if not self.failures:
            raise NodeExecutionContractError("failed results require at least one failure")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _AwaitingResumeGraphResult(Generic[GraphValueT]):
    state: GraphRunState
    continuation: _GraphContinuation[GraphValueT]
    interrupts: tuple[GraphInterruptView, ...]
    _seal: InitVar[_ResultSeal]

    def __post_init__(self, _seal: _ResultSeal) -> None:
        if _seal is not _RESULT_SEAL:
            raise NodeExecutionContractError("awaiting-resume results require the graph family driver")
        if not self.interrupts:
            raise NodeExecutionContractError("awaiting-resume results require at least one interrupt")


GraphResult: TypeAlias = (
    _CompletedGraphResult[GraphValueT]
    | _FailedGraphResult[GraphValueT]
    | _AbortedGraphResult[GraphValueT]
    | _AwaitingResumeGraphResult[GraphValueT]
)


def _completed_result(
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
    outputs: _GraphValues[GraphValueT],
) -> _CompletedGraphResult[GraphValueT]:
    return _CompletedGraphResult(
        state=state,
        continuation=continuation,
        outputs=outputs,
        _seal=_RESULT_SEAL,
    )


def _aborted_result(
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
    abort: GraphAbortView,
) -> _AbortedGraphResult[GraphValueT]:
    return _AbortedGraphResult(
        state=state,
        continuation=continuation,
        abort=abort,
        _seal=_RESULT_SEAL,
    )


def _failed_result(
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
    failures: tuple[GraphFailureView, ...],
    interrupts: tuple[GraphInterruptView, ...],
) -> _FailedGraphResult[GraphValueT]:
    return _FailedGraphResult(
        state=state,
        continuation=continuation,
        failures=failures,
        interrupts=interrupts,
        _seal=_RESULT_SEAL,
    )


def _awaiting_result(
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
    interrupts: tuple[GraphInterruptView, ...],
) -> _AwaitingResumeGraphResult[GraphValueT]:
    return _AwaitingResumeGraphResult(
        state=state,
        continuation=continuation,
        interrupts=interrupts,
        _seal=_RESULT_SEAL,
    )


__all__ = [
    "_AbortedGraphResult",
    "_AwaitingResumeGraphResult",
    "_CompiledFamilyIdentity",
    "_CompletedGraphResult",
    "_FailedGraphResult",
    "_GraphContinuation",
    "_PartialCommitError",
    "_aborted_result",
    "_admit_continuation",
    "_awaiting_result",
    "_completed_result",
    "_failed_result",
    "_make_continuation",
    "_partial_commit_error",
]

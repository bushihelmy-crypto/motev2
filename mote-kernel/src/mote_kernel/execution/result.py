"""Execution results, public commit evidence, and graph dispositions."""

from dataclasses import InitVar, dataclass
from typing import Generic, TypeAlias, TypeVar, final

from mote_kernel.execution.claim import PreparedExecutionClaim
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ExecutionError, NodeExecutionContractError, SnapshotMismatchError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    GraphOutputView,
    NodeOutputFrame,
    _GraphValues,
    _public_node_output,
)
from mote_kernel.execution.run_context import AdmittedResumeInput, PreparedSubstitution, _GraphContinuation
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    GraphInterruptId,
    GraphNodeId,
    GraphRunState,
    ParentGraphActivation,
    ResumeGraphNodes,
    SettleGraphNode,
    StartGraphRun,
)

GraphValueT = TypeVar("GraphValueT")


class _PartialCommitSeal:
    __slots__ = ()


_PARTIAL_COMMIT_SEAL = _PartialCommitSeal()


@final
class _PartialCommitError(ExecutionError, Generic[GraphValueT]):
    """Explicit handoff for an invocation that durably confirmed only a prefix."""

    __slots__ = ("cause", "continuation", "failed_scope", "state")

    def __init__(
        self,
        *,
        state: GraphRunState,
        continuation: _GraphContinuation[GraphValueT],
        cause: Exception,
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
    cause: Exception,
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
class TaskSuccess(Generic[GraphValueT]):
    task: GraphTask
    output: NodeOutputFrame[GraphValueT]
    route: str | None


@dataclass(frozen=True, slots=True)
class TaskFailure:
    task: GraphTask
    failure: str


@dataclass(frozen=True, slots=True)
class TaskInterrupt:
    task: GraphTask
    request_payload: bytes


TaskResult: TypeAlias = TaskSuccess[GraphValueT] | TaskFailure | TaskInterrupt


class _CommitResultSeal:
    __slots__ = ()


_COMMIT_RESULT_SEAL = _CommitResultSeal()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphSuccessResult(Generic[GraphValueT]):
    node_id: str
    output: _GraphValues[GraphValueT]
    route: str | None
    _seal: InitVar[_CommitResultSeal]

    def __post_init__(self, _seal: _CommitResultSeal) -> None:
        if _seal is not _COMMIT_RESULT_SEAL:
            raise NodeExecutionContractError("success commit results require settlement admission")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphFailureResult:
    node_id: str
    failure: str
    _seal: InitVar[_CommitResultSeal]

    def __post_init__(self, _seal: _CommitResultSeal) -> None:
        if _seal is not _COMMIT_RESULT_SEAL:
            raise NodeExecutionContractError("failure commit results require settlement admission")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphInterruptResult:
    node_id: str
    request_payload: bytes
    _seal: InitVar[_CommitResultSeal]

    def __post_init__(self, _seal: _CommitResultSeal) -> None:
        if _seal is not _COMMIT_RESULT_SEAL:
            raise NodeExecutionContractError("interrupt commit results require settlement admission")


GraphCommitResult: TypeAlias = _GraphSuccessResult[GraphValueT] | _GraphFailureResult | _GraphInterruptResult


def _commit_result(result: TaskResult[GraphValueT]) -> GraphCommitResult[GraphValueT]:
    if type(result) not in (TaskSuccess, TaskFailure, TaskInterrupt):
        raise NodeExecutionContractError("task result has an unsupported variant")
    if isinstance(result, TaskSuccess):
        return _GraphSuccessResult(
            node_id=result.task.node_id,
            output=_public_node_output(result.output),
            route=result.route,
            _seal=_COMMIT_RESULT_SEAL,
        )
    if isinstance(result, TaskFailure):
        return _GraphFailureResult(
            node_id=result.task.node_id,
            failure=result.failure,
            _seal=_COMMIT_RESULT_SEAL,
        )
    return _GraphInterruptResult(
        node_id=result.task.node_id,
        request_payload=result.request_payload,
        _seal=_COMMIT_RESULT_SEAL,
    )


@dataclass(frozen=True, slots=True)
class MissingChild:
    parent: ParentGraphActivation


@dataclass(frozen=True, slots=True)
class ActiveChild:
    parent: ParentGraphActivation
    child_state: GraphRunState


@dataclass(frozen=True, slots=True)
class CompletedChild(Generic[GraphValueT]):
    parent: ParentGraphActivation
    child_state: GraphRunState
    output: GraphOutputView[GraphValueT]


@dataclass(frozen=True, slots=True)
class AbortedChild:
    parent: ParentGraphActivation
    child_state: GraphRunState


ChildProjection: TypeAlias = MissingChild | ActiveChild | CompletedChild[GraphValueT] | AbortedChild


@dataclass(frozen=True, slots=True)
class PreparedNestedRun(Generic[GraphValueT]):
    parent: ParentGraphActivation
    graph: CompiledGraph[GraphValueT]
    command: StartGraphRun


@dataclass(frozen=True, slots=True)
class StartMissingChildren(Generic[GraphValueT]):
    children: tuple[PreparedNestedRun[GraphValueT], ...]

    def __post_init__(self) -> None:
        parents = tuple(child.parent for child in self.children)
        if (
            not parents
            or len(parents) != len(set(parents))
            or parents != tuple(sorted(parents, key=lambda parent: (parent.run_id, parent.superstep, parent.node_id)))
        ):
            raise ValueError("children to start must be non-empty and canonical")


@dataclass(frozen=True, slots=True)
class WaitForActiveChildren:
    children: tuple[ActiveChild, ...]

    def __post_init__(self) -> None:
        parents = tuple(child.parent for child in self.children)
        if (
            not parents
            or len(parents) != len(set(parents))
            or parents != tuple(sorted(parents, key=lambda parent: (parent.run_id, parent.superstep, parent.node_id)))
        ):
            raise ValueError("active children must be non-empty and canonical")


ChildWaitAction: TypeAlias = StartMissingChildren[GraphValueT] | WaitForActiveChildren


@dataclass(frozen=True, slots=True)
class WaitingForChildren(Generic[GraphValueT]):
    action: ChildWaitAction[GraphValueT]


@dataclass(frozen=True, slots=True)
class ExecutableFrontier:
    claim: PreparedExecutionClaim


@dataclass(frozen=True, slots=True)
class ReadyToResolve:
    command: AdvanceGraphFrontier | CompleteGraphFrontier | AbortGraphRun


@dataclass(frozen=True, slots=True)
class AwaitingResume:
    failed_node_ids: tuple[GraphNodeId, ...]
    interrupted_node_ids: tuple[GraphNodeId, ...]


@dataclass(frozen=True, slots=True)
class CompletedGraph:
    pass


@dataclass(frozen=True, slots=True)
class AbortedGraph:
    pass


GraphBoundary: TypeAlias = AwaitingResume | CompletedGraph | AbortedGraph


PrepareDisposition: TypeAlias = (
    ExecutableFrontier
    | WaitingForChildren[GraphValueT]
    | ReadyToResolve
    | AwaitingResume
    | CompletedGraph
    | AbortedGraph
)


@dataclass(frozen=True, slots=True)
class ExecutedGraphNode(Generic[GraphValueT]):
    result: TaskResult[GraphValueT]
    command: SettleGraphNode


@dataclass(frozen=True, slots=True)
class PreparedResume(Generic[GraphValueT]):
    command: "ResumeGraphNodes"
    inputs: tuple[AdmittedResumeInput[GraphValueT], ...]
    substitutions: tuple[PreparedSubstitution[GraphValueT], ...]


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
class _AwaitingResumeGraphResult(Generic[GraphValueT]):
    state: GraphRunState
    continuation: _GraphContinuation[GraphValueT]
    failures: tuple[GraphFailureView, ...]
    interrupts: tuple[GraphInterruptView, ...]
    _seal: InitVar[_ResultSeal]

    def __post_init__(self, _seal: _ResultSeal) -> None:
        if _seal is not _RESULT_SEAL:
            raise NodeExecutionContractError("awaiting-resume results require the graph family driver")


GraphResult: TypeAlias = (
    _CompletedGraphResult[GraphValueT] | _AbortedGraphResult[GraphValueT] | _AwaitingResumeGraphResult[GraphValueT]
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


def _awaiting_result(
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
    failures: tuple[GraphFailureView, ...],
    interrupts: tuple[GraphInterruptView, ...],
) -> _AwaitingResumeGraphResult[GraphValueT]:
    return _AwaitingResumeGraphResult(
        state=state,
        continuation=continuation,
        failures=failures,
        interrupts=interrupts,
        _seal=_RESULT_SEAL,
    )


__all__ = [
    "_AbortedGraphResult",
    "_AwaitingResumeGraphResult",
    "_CompletedGraphResult",
    "_GraphFailureResult",
    "_GraphInterruptResult",
    "_GraphSuccessResult",
    "_PartialCommitError",
    "_aborted_result",
    "_awaiting_result",
    "_commit_result",
    "_completed_result",
    "_partial_commit_error",
]

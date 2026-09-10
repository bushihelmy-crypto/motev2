"""Task outcomes, settlement evidence, and graph execution dispositions."""

from dataclasses import InitVar, dataclass
from typing import Generic, TypeAlias, TypeVar, final

from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import NodeExecutionContractError
from mote_kernel.execution.graph.values import (
    GraphOutputView,
    NodeOutputFrame,
    _GraphValues,
    _public_values,
)
from mote_kernel.execution.run_context import AdmittedResumeInput, GraphPublicationEvidence
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    AdvanceGraphFrontier,
    CompleteGraphFrontier,
    GraphAbortReason,
    GraphActivationIdentity,
    GraphNodeId,
    ResumeGraphNodes,
    SettleGraphNode,
)

GraphValueT = TypeVar("GraphValueT")
SUPERSEDED_CHILD_ABORT_REASON = GraphAbortReason("nested graph was superseded by a sibling failure")


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
    publication: GraphPublicationEvidence[GraphValueT]
    route: str | None
    _seal: InitVar[_CommitResultSeal]

    def __post_init__(self, _seal: _CommitResultSeal) -> None:
        if _seal is not _COMMIT_RESULT_SEAL:
            raise NodeExecutionContractError("success commit results require settlement admission")

    @property
    def output(self) -> _GraphValues[GraphValueT]:
        """Expose the publication values without storing a second payload."""

        return _public_values(self.publication.frame)


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


def _commit_result(
    result: TaskResult[GraphValueT],
    publication: GraphPublicationEvidence[GraphValueT] | None,
) -> GraphCommitResult[GraphValueT]:
    if type(result) not in (TaskSuccess, TaskFailure, TaskInterrupt):
        raise NodeExecutionContractError("task result has an unsupported variant")
    if isinstance(result, TaskSuccess):
        if publication is None:
            raise NodeExecutionContractError("successful settlement requires publication evidence")
        if publication.frame is not result.output:
            raise NodeExecutionContractError("publication evidence must carry the task output frame")
        return _GraphSuccessResult(
            node_id=result.task.node_id,
            publication=publication,
            route=result.route,
            _seal=_COMMIT_RESULT_SEAL,
        )
    if publication is not None:
        raise NodeExecutionContractError("failed or interrupted settlement cannot publish output evidence")
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
    parent: GraphActivationIdentity


@dataclass(frozen=True, slots=True)
class ActiveChild:
    parent: GraphActivationIdentity


@dataclass(frozen=True, slots=True)
class CompletedChild(Generic[GraphValueT]):
    parent: GraphActivationIdentity
    output: GraphOutputView[GraphValueT]
    # A completed nested graph may expose the route selected by its terminal
    # node.  The route is intentionally opaque to the child projection; the
    # parent graph validates it against its own conditional edge domain.
    route: str | None = None


@dataclass(frozen=True, slots=True)
class FailedChild:
    parent: GraphActivationIdentity
    failure: str


@dataclass(frozen=True, slots=True)
class AbortedChild:
    parent: GraphActivationIdentity
    reason: GraphAbortReason


ChildProjection: TypeAlias = MissingChild | ActiveChild | CompletedChild[GraphValueT] | FailedChild | AbortedChild


@dataclass(frozen=True, slots=True)
class WaitingForChildren(Generic[GraphValueT]):
    missing: tuple[MissingChild, ...]
    active: tuple[ActiveChild, ...]

    def __post_init__(self) -> None:
        missing_parents = tuple(projection.parent for projection in self.missing)
        active_parents = tuple(projection.parent for projection in self.active)
        parents = (*missing_parents, *active_parents)
        if (
            not parents
            or len(parents) != len(set(parents))
            or missing_parents
            != tuple(sorted(missing_parents, key=lambda parent: (parent.run_id, parent.superstep, parent.node_id)))
            or active_parents
            != tuple(sorted(active_parents, key=lambda parent: (parent.run_id, parent.superstep, parent.node_id)))
        ):
            raise ValueError("children to drive must be non-empty, distinct, and canonical")


@dataclass(frozen=True, slots=True)
class ReadyToResolve:
    command: AdvanceGraphFrontier | CompleteGraphFrontier | AbortGraphRun


@dataclass(frozen=True, slots=True)
class AwaitingResume:
    interrupted_node_ids: tuple[GraphNodeId, ...]


@dataclass(frozen=True, slots=True)
class CompletedGraph:
    pass


@dataclass(frozen=True, slots=True)
class FailedGraph:
    pass


@dataclass(frozen=True, slots=True)
class AbortedGraph:
    pass


GraphBoundary: TypeAlias = AwaitingResume | CompletedGraph | FailedGraph | AbortedGraph


@dataclass(frozen=True, slots=True)
class ExecutedGraphNode(Generic[GraphValueT]):
    result: TaskResult[GraphValueT]
    command: SettleGraphNode


@dataclass(frozen=True, slots=True)
class PreparedResume(Generic[GraphValueT]):
    command: "ResumeGraphNodes"
    inputs: tuple[AdmittedResumeInput[GraphValueT], ...]


__all__ = [
    "_GraphFailureResult",
    "_GraphInterruptResult",
    "_GraphSuccessResult",
    "_commit_result",
]

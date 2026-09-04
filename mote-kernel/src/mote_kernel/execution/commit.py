"""Authoritative graph transition and atomic write-set contract."""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from typing import Generic, Protocol, TypeVar, final

from mote_kernel.execution.engine.routing import transition_admission_error
from mote_kernel.execution.errors import FrameInstallationInvariantError, SnapshotMismatchError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import GraphInputFrame
from mote_kernel.execution.identity import ScopeRunCoordinate, stable_activation
from mote_kernel.execution.result import (
    GraphCommitResult,
    TaskResult,
    TaskSuccess,
    _commit_result,
    _GraphFailureResult,
    _GraphInterruptResult,
    _GraphSuccessResult,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    GraphInputEvidence,
    GraphPublicationEvidence,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    GraphActivationIdentity,
    GraphRunCommand,
    GraphRunId,
    GraphRunState,
    SettleGraphNode,
    StartGraphRun,
    reduce_graph_run,
)
from mote_kernel.state.graph_state.identity import is_canonical_identity

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True, order=True)
class GraphCommitKey:
    """Stable identity for one candidate state offered to persistence."""

    run_id: GraphRunId
    revision: int

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.run_id):
            raise SnapshotMismatchError("commit key requires a canonical run identity")
        if type(self.revision) is not int or self.revision < 0:
            raise SnapshotMismatchError("commit key revision must be a non-negative integer")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class GraphCommitWriteSet(Generic[GraphValueT]):
    """Immutable frame and settlement facts for one atomic graph commit."""

    commit_key: GraphCommitKey
    graph_inputs: tuple[GraphInputEvidence[GraphValueT], ...] = ()
    settlement: GraphCommitResult[GraphValueT] | None = None

    def __post_init__(self) -> None:
        if type(self.commit_key) is not GraphCommitKey:
            raise SnapshotMismatchError("commit write set has an invalid commit key")
        if type(self.graph_inputs) is not tuple or any(
            type(item) is not GraphInputEvidence for item in self.graph_inputs
        ):
            raise SnapshotMismatchError("commit graph inputs must be a typed immutable tuple")
        coordinates = tuple(item.coordinate for item in self.graph_inputs)
        if len(coordinates) != len(set(coordinates)) or coordinates != tuple(sorted(coordinates)):
            raise SnapshotMismatchError("commit graph inputs must be canonical and distinct")
        if self.settlement is not None and type(self.settlement) not in (
            _GraphSuccessResult,
            _GraphFailureResult,
            _GraphInterruptResult,
        ):
            raise SnapshotMismatchError("commit settlement has an unsupported variant")

    @property
    def publications(self) -> tuple[GraphPublicationEvidence[GraphValueT], ...]:
        """Return the success publication as a write projection, if present."""

        settlement = self.settlement
        if type(settlement) is _GraphSuccessResult:
            return (settlement.publication,)
        return ()


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
    writes: GraphCommitWriteSet[GraphValueT]
    _seal: InitVar[_TransitionSeal]

    def __post_init__(self, _seal: _TransitionSeal) -> None:
        if _seal is not _TRANSITION_SEAL:
            raise SnapshotMismatchError("graph transitions can only be produced by the execution commit owner")
        if type(self.writes) is not GraphCommitWriteSet:
            raise SnapshotMismatchError("graph transition has an invalid commit write set")
        if (
            self.writes.commit_key.run_id != self.candidate_state.run_id
            or self.writes.commit_key.revision != self.candidate_state.revision
        ):
            raise SnapshotMismatchError("graph write set is not bound to the candidate state")
        if isinstance(self.command, StartGraphRun):
            if self.writes.settlement is not None or self.writes.publications:
                raise SnapshotMismatchError("StartGraphRun cannot carry settlement output evidence")
            if len(self.writes.graph_inputs) != 1:
                raise SnapshotMismatchError("StartGraphRun requires exactly one graph input evidence")
        elif self.writes.graph_inputs:
            raise SnapshotMismatchError("only StartGraphRun can carry graph input evidence")
        if isinstance(self.command, SettleGraphNode):
            settlement = self.writes.settlement
            if settlement is None or settlement.node_id != self.command.outcome.node_id:
                raise SnapshotMismatchError("settlement evidence does not match its command")
            if type(settlement) is _GraphSuccessResult:
                if len(self.writes.publications) != 1:
                    raise SnapshotMismatchError("successful settlement requires exactly one publication evidence")
            elif self.writes.publications:
                raise SnapshotMismatchError("failed or interrupted settlement cannot publish output evidence")
        elif self.writes.settlement is not None or self.writes.publications:
            raise SnapshotMismatchError("only SettleGraphNode can carry settlement evidence")


class GraphCommit(Protocol[GraphValueT]):
    async def __call__(
        self,
        transition: GraphTransition[GraphValueT],
        /,
    ) -> GraphRunState: ...


def prepare_transition(
    scope_run: ScopeRunCoordinate,
    previous_state: GraphRunState | None,
    command: GraphRunCommand,
    result: TaskResult[GraphValueT] | None,
    *,
    graph: CompiledGraph[GraphValueT],
    admitted_successor: GraphRunState | None = None,
    graph_input: GraphInputFrame[GraphValueT] | None = None,
) -> GraphTransition[GraphValueT]:
    """Build one sealed transition and its complete immutable write set."""

    candidate = reduce_graph_run(previous_state, command)
    if admission_error := transition_admission_error(graph, previous_state, command, candidate):
        raise SnapshotMismatchError(admission_error)
    if admitted_successor is not None and candidate != admitted_successor:
        raise FrameInstallationInvariantError("owner resume candidate does not match its admitted successor")
    if isinstance(command, StartGraphRun):
        if graph_input is None:
            raise FrameInstallationInvariantError("StartGraphRun requires graph input evidence")
        input_evidence = (
            GraphInputEvidence(
                GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
                graph_input,
            ),
        )
    elif graph_input is not None:
        raise FrameInstallationInvariantError("only StartGraphRun can carry graph input evidence")
    else:
        input_evidence = ()

    publication: GraphPublicationEvidence[GraphValueT] | None = None
    if isinstance(command, SettleGraphNode):
        if result is None:
            raise FrameInstallationInvariantError("SettleGraphNode requires settlement evidence")
        task = result.task
        if (
            task.run_id != candidate.run_id
            or task.superstep != candidate.superstep
            or task.node_id != command.outcome.node_id
        ):
            raise SnapshotMismatchError("settlement result does not match its command coordinates")
        if isinstance(result, TaskSuccess):
            descriptor = graph.transition.publications[task.node_id]
            publication = GraphPublicationEvidence(
                PublicationAvailabilityCoordinate(
                    stable_activation(
                        scope_run,
                        GraphActivationIdentity(task.run_id, task.superstep, task.node_id),
                    ),
                    descriptor.identity,
                ),
                result.output,
                ExecutionPublicationProvenance(command.execution),
            )
    elif result is not None:
        raise FrameInstallationInvariantError("only SettleGraphNode can carry settlement evidence")

    writes = GraphCommitWriteSet(
        commit_key=GraphCommitKey(candidate.run_id, candidate.revision),
        graph_inputs=input_evidence,
        settlement=_commit_result(result, publication) if result is not None else None,
    )
    return GraphTransition(
        scope=tuple(scope_run.scope),
        previous_state=previous_state,
        command=command,
        candidate_state=candidate,
        writes=writes,
        _seal=_TRANSITION_SEAL,
    )


async def confirm_transition(
    transition: GraphTransition[GraphValueT],
    commit: GraphCommit[GraphValueT],
) -> GraphRunState:
    confirmed = await commit(transition)
    if type(confirmed) is not GraphRunState or confirmed != transition.candidate_state:
        raise SnapshotMismatchError("commit must return the exact authoritative reducer successor")
    return confirmed


async def commit_transition(
    scope_run: ScopeRunCoordinate,
    previous_state: GraphRunState | None,
    command: GraphRunCommand,
    result: TaskResult[GraphValueT] | None,
    commit: GraphCommit[GraphValueT],
    *,
    graph: CompiledGraph[GraphValueT],
    admitted_successor: GraphRunState | None = None,
    graph_input: GraphInputFrame[GraphValueT] | None = None,
) -> GraphRunState:
    """Reduce, expose, and confirm one authoritative state transition."""

    transition = prepare_transition(
        scope_run,
        previous_state,
        command,
        result,
        graph=graph,
        admitted_successor=admitted_successor,
        graph_input=graph_input,
    )
    return await confirm_transition(transition, commit)


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


def apply_commit_writes(
    frames: ScopedFrameIndex[GraphValueT],
    writes: GraphCommitWriteSet[GraphValueT],
) -> ScopedFrameIndex[GraphValueT]:
    """Stage committed frame facts without mutating the live frame index."""

    staged = frames
    for evidence in writes.graph_inputs:
        staged = staged.add_graph_input(AdmittedGraphInput(evidence.coordinate, evidence.frame))
    for evidence in writes.publications:
        staged = staged.add_publication(
            ConfirmedPublication(
                evidence.coordinate,
                evidence.frame,
                writes.commit_key.revision,
                evidence.provenance,
            )
        )
    return staged


__all__: list[str] = []

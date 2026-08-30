"""Invocation-local scoped frames and opaque continuation snapshots."""

from dataclasses import InitVar, dataclass, field
from typing import Generic, Never, Protocol, SupportsIndex, TypeAlias, TypeVar, final, overload

from mote_kernel.execution.errors import (
    GraphValuePublicationError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity
from mote_kernel.execution.graph.values import (
    GraphInputFrame,
    GraphOutputView,
    NodeInputFrame,
    NodeOutputFrame,
)
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.state.graph_state import GraphExecutionToken, GraphRunState

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True, order=True)
class GraphInputAvailabilityCoordinate(Generic[GraphValueT]):
    scope_run: ScopeRunCoordinate
    descriptor: FrameDescriptorIdentity


@dataclass(frozen=True, slots=True, order=True)
class PublicationAvailabilityCoordinate(Generic[GraphValueT]):
    activation: StableActivation
    descriptor: FrameDescriptorIdentity


@dataclass(frozen=True, slots=True, order=True)
class ResumeInputAvailabilityCoordinate(Generic[GraphValueT]):
    activation: StableActivation
    descriptor: FrameDescriptorIdentity


@dataclass(frozen=True, slots=True, order=True)
class ChildBoundaryAvailabilityCoordinate(Generic[GraphValueT]):
    child_scope_run: ScopeRunCoordinate
    descriptor: FrameDescriptorIdentity


@dataclass(frozen=True, slots=True, eq=False)
class AdmittedGraphInput(Generic[GraphValueT]):
    coordinate: GraphInputAvailabilityCoordinate[GraphValueT]
    frame: GraphInputFrame[GraphValueT] = field(compare=False, repr=False, hash=False)

    def __hash__(self) -> Never:
        raise TypeError("scoped frame records are unhashable")


@dataclass(frozen=True, slots=True)
class ExecutionPublicationProvenance:
    execution_token: GraphExecutionToken


@dataclass(frozen=True, slots=True)
class SkipSubstitutionProvenance:
    pass


PublicationProvenance: TypeAlias = ExecutionPublicationProvenance | SkipSubstitutionProvenance


@dataclass(frozen=True, slots=True, eq=False)
class ConfirmedPublication(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT] = field(compare=False, repr=False, hash=False)
    acknowledged_revision: int
    provenance: PublicationProvenance

    def __hash__(self) -> Never:
        raise TypeError("scoped frame records are unhashable")


@dataclass(frozen=True, slots=True)
class PreparedSubstitution(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT]
    provenance: SkipSubstitutionProvenance


@dataclass(frozen=True, slots=True)
class AdmittedSubstitution(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT]
    provenance: SkipSubstitutionProvenance
    expected_revision: int


@dataclass(frozen=True, slots=True, eq=False)
class AdmittedResumeInput(Generic[GraphValueT]):
    coordinate: ResumeInputAvailabilityCoordinate[GraphValueT]
    frame: NodeInputFrame[GraphValueT] = field(compare=False, repr=False, hash=False)

    def __hash__(self) -> Never:
        raise TypeError("scoped frame records are unhashable")


@dataclass(frozen=True, slots=True, eq=False)
class ConfirmedChildBoundary(Generic[GraphValueT]):
    coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT]
    frame: GraphOutputView[GraphValueT] = field(compare=False, repr=False, hash=False)

    def __hash__(self) -> Never:
        raise TypeError("scoped frame records are unhashable")


FrameCoordinate: TypeAlias = (
    GraphInputAvailabilityCoordinate[GraphValueT]
    | PublicationAvailabilityCoordinate[GraphValueT]
    | ResumeInputAvailabilityCoordinate[GraphValueT]
    | ChildBoundaryAvailabilityCoordinate[GraphValueT]
)
FrameRecord: TypeAlias = (
    AdmittedGraphInput[GraphValueT]
    | ConfirmedPublication[GraphValueT]
    | AdmittedResumeInput[GraphValueT]
    | ConfirmedChildBoundary[GraphValueT]
)


class ScopedFrameAvailability(Protocol[GraphValueT]):
    def has_graph_input(
        self,
        coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
    ) -> bool: ...

    def has_publication(
        self,
        coordinate: PublicationAvailabilityCoordinate[GraphValueT],
    ) -> bool: ...

    def has_resume_input(
        self,
        coordinate: ResumeInputAvailabilityCoordinate[GraphValueT],
    ) -> bool: ...

    def has_child_boundary(
        self,
        coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class CandidateFrameAvailability(Generic[GraphValueT]):
    confirmed: "ScopedFrameIndex[GraphValueT]"
    substitutions: tuple[AdmittedSubstitution[GraphValueT], ...]

    def has_graph_input(self, coordinate: GraphInputAvailabilityCoordinate[GraphValueT]) -> bool:
        return self.confirmed.has_graph_input(coordinate)

    def has_publication(self, coordinate: PublicationAvailabilityCoordinate[GraphValueT]) -> bool:
        return self.confirmed.has_publication(coordinate) or any(
            substitution.coordinate == coordinate for substitution in self.substitutions
        )

    def has_resume_input(self, coordinate: ResumeInputAvailabilityCoordinate[GraphValueT]) -> bool:
        return self.confirmed.has_resume_input(coordinate)

    def has_child_boundary(self, coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT]) -> bool:
        return self.confirmed.has_child_boundary(coordinate)


@dataclass(frozen=True, slots=True, eq=False)
class ScopedFrameIndex(Generic[GraphValueT]):
    graph_inputs: tuple[AdmittedGraphInput[GraphValueT], ...] = ()
    publications: tuple[ConfirmedPublication[GraphValueT], ...] = ()
    resume_inputs: tuple[AdmittedResumeInput[GraphValueT], ...] = ()
    child_boundaries: tuple[ConfirmedChildBoundary[GraphValueT], ...] = ()

    def __hash__(self) -> Never:
        raise TypeError("scoped frame indexes are unhashable")

    def has_graph_input(
        self,
        coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return any(record.coordinate == coordinate for record in self.graph_inputs)

    def has_publication(
        self,
        coordinate: PublicationAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return any(record.coordinate == coordinate for record in self.publications)

    def has_resume_input(
        self,
        coordinate: ResumeInputAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return any(record.coordinate == coordinate for record in self.resume_inputs)

    def has_child_boundary(
        self,
        coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT],
    ) -> bool:
        return any(record.coordinate == coordinate for record in self.child_boundaries)

    @overload
    def lookup(
        self,
        coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
    ) -> AdmittedGraphInput[GraphValueT]: ...

    @overload
    def lookup(
        self,
        coordinate: PublicationAvailabilityCoordinate[GraphValueT],
    ) -> ConfirmedPublication[GraphValueT]: ...

    @overload
    def lookup(
        self,
        coordinate: ResumeInputAvailabilityCoordinate[GraphValueT],
    ) -> AdmittedResumeInput[GraphValueT]: ...

    @overload
    def lookup(
        self,
        coordinate: ChildBoundaryAvailabilityCoordinate[GraphValueT],
    ) -> ConfirmedChildBoundary[GraphValueT]: ...

    def lookup(
        self,
        coordinate: FrameCoordinate[GraphValueT],
    ) -> FrameRecord[GraphValueT]:
        if isinstance(coordinate, GraphInputAvailabilityCoordinate):
            for record in self.graph_inputs:
                if record.coordinate == coordinate:
                    return record
        elif isinstance(coordinate, PublicationAvailabilityCoordinate):
            for record in self.publications:
                if record.coordinate == coordinate:
                    return record
        elif isinstance(coordinate, ResumeInputAvailabilityCoordinate):
            for record in self.resume_inputs:
                if record.coordinate == coordinate:
                    return record
        else:
            for record in self.child_boundaries:
                if record.coordinate == coordinate:
                    return record
        raise SnapshotMismatchError(f"continuation has no frame at coordinate {coordinate!r}")

    def add_graph_input(
        self,
        record: AdmittedGraphInput[GraphValueT],
    ) -> "ScopedFrameIndex[GraphValueT]":
        if any(existing.coordinate == record.coordinate for existing in self.graph_inputs):
            raise GraphValuePublicationError("graph input coordinate was admitted more than once")
        return ScopedFrameIndex(
            graph_inputs=tuple(sorted((*self.graph_inputs, record), key=lambda item: item.coordinate)),
            publications=self.publications,
            resume_inputs=self.resume_inputs,
            child_boundaries=self.child_boundaries,
        )

    def add_publication(
        self,
        record: ConfirmedPublication[GraphValueT],
    ) -> "ScopedFrameIndex[GraphValueT]":
        if any(existing.coordinate == record.coordinate for existing in self.publications):
            raise GraphValuePublicationError("stable activation was published more than once")
        return ScopedFrameIndex(
            graph_inputs=self.graph_inputs,
            publications=tuple(sorted((*self.publications, record), key=lambda item: item.coordinate)),
            resume_inputs=self.resume_inputs,
            child_boundaries=self.child_boundaries,
        )

    def add_resume_input(
        self,
        record: AdmittedResumeInput[GraphValueT],
    ) -> "ScopedFrameIndex[GraphValueT]":
        if any(existing.coordinate == record.coordinate for existing in self.resume_inputs):
            raise GraphValuePublicationError("resume input coordinate was admitted more than once")
        return ScopedFrameIndex(
            graph_inputs=self.graph_inputs,
            publications=self.publications,
            resume_inputs=tuple(sorted((*self.resume_inputs, record), key=lambda item: item.coordinate)),
            child_boundaries=self.child_boundaries,
        )

    def add_child_boundary(
        self,
        record: ConfirmedChildBoundary[GraphValueT],
    ) -> "ScopedFrameIndex[GraphValueT]":
        if any(existing.coordinate == record.coordinate for existing in self.child_boundaries):
            raise GraphValuePublicationError("child boundary coordinate was confirmed more than once")
        return ScopedFrameIndex(
            graph_inputs=self.graph_inputs,
            publications=self.publications,
            resume_inputs=self.resume_inputs,
            child_boundaries=tuple(sorted((*self.child_boundaries, record), key=lambda item: item.coordinate)),
        )


@dataclass(frozen=True, slots=True, eq=False)
class _CompiledFamilyIdentity:
    pass


@dataclass(frozen=True, slots=True)
class ChildStateBinding:
    coordinate: ScopeRunCoordinate
    parent_activation: StableActivation
    state: GraphRunState


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class _CompleteContinuationSnapshot(Generic[GraphValueT]):
    family_identity: _CompiledFamilyIdentity
    root_state: GraphRunState
    child_states: tuple[ChildStateBinding, ...]
    frames: ScopedFrameIndex[GraphValueT]


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class _RecoveredContinuationSnapshot(Generic[GraphValueT]):
    family_identity: _CompiledFamilyIdentity
    root_state: GraphRunState
    child_states: tuple[ChildStateBinding, ...]
    frames: ScopedFrameIndex[GraphValueT]


ContinuationSnapshot: TypeAlias = (
    _CompleteContinuationSnapshot[GraphValueT] | _RecoveredContinuationSnapshot[GraphValueT]
)


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
    ) -> ContinuationSnapshot[GraphValueT]:
        if _seal is not _CONTINUATION_SEAL:
            raise SnapshotMismatchError("continuations can only be admitted by their Graph owner")
        snapshot = self._snapshot
        if snapshot.family_identity is not family_identity or snapshot.root_state != state:
            raise SnapshotMismatchError("state and continuation do not belong to the same compiled graph lineage")
        return snapshot

    def __copy__(self) -> Never:
        raise SnapshotMismatchError("continuations do not provide a copy contract")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> Never:
        raise SnapshotMismatchError("continuations do not provide a serialization contract")


def _admit_continuation(
    family_identity: _CompiledFamilyIdentity,
    state: GraphRunState,
    continuation: _GraphContinuation[GraphValueT],
) -> ContinuationSnapshot[GraphValueT]:
    if type(continuation) is not _GraphContinuation:
        raise SnapshotMismatchError("continuations can only be admitted by their Graph owner")
    return continuation.admit_snapshot(_CONTINUATION_SEAL, family_identity, state)


def _continuation_recovered(snapshot: ContinuationSnapshot[GraphValueT]) -> bool:
    return isinstance(snapshot, _RecoveredContinuationSnapshot)


def _make_continuation(
    family_identity: _CompiledFamilyIdentity,
    root_state: GraphRunState,
    child_states: tuple[ChildStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
    *,
    recovered: bool,
) -> _GraphContinuation[GraphValueT]:
    snapshot: ContinuationSnapshot[GraphValueT]
    if recovered:
        snapshot = _RecoveredContinuationSnapshot(family_identity, root_state, child_states, frames)
    else:
        snapshot = _CompleteContinuationSnapshot(family_identity, root_state, child_states, frames)
    return _GraphContinuation(_snapshot=snapshot, _seal=_CONTINUATION_SEAL)


__all__ = [
    "_CompiledFamilyIdentity",
    "_GraphContinuation",
    "_admit_continuation",
    "_continuation_recovered",
    "_make_continuation",
]

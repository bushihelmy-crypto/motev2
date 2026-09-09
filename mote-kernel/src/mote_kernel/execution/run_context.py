"""Invocation-local scoped frames and opaque continuation snapshots."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from dataclasses import InitVar, dataclass, field, replace
from typing import Generic, Never, Protocol, Self, SupportsIndex, TypeAlias, TypeVar, cast, final, overload

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
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation, parent_activation_for_child
from mote_kernel.state.graph_state import GraphExecutionToken, GraphRunState

GraphValueT = TypeVar("GraphValueT")


class _ComparableCoordinate(Protocol):
    """Ordering contract for the homogeneous coordinate partitions."""

    def __lt__(self, other: Self, /) -> bool: ...

    def __gt__(self, other: Self, /) -> bool: ...


FrameRecordT = TypeVar("FrameRecordT")
CoordinateT = TypeVar("CoordinateT")


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
class GraphInputEvidence(Generic[GraphValueT]):
    """A graph input staged for the same commit as its Start transition."""

    coordinate: GraphInputAvailabilityCoordinate[GraphValueT]
    frame: GraphInputFrame[GraphValueT] = field(compare=False, repr=False, hash=False)

    def __post_init__(self) -> None:
        if type(self.coordinate) is not GraphInputAvailabilityCoordinate or type(self.frame) is not GraphInputFrame:
            raise SnapshotMismatchError("graph input evidence has an invalid typed frame")

    def __hash__(self) -> Never:
        raise TypeError("graph input evidence is unhashable")


@dataclass(frozen=True, slots=True, eq=False)
class GraphPublicationEvidence(Generic[GraphValueT]):
    """A node output staged for the same commit as its settlement."""

    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT] = field(compare=False, repr=False, hash=False)
    provenance: ExecutionPublicationProvenance

    def __post_init__(self) -> None:
        if (
            type(self.coordinate) is not PublicationAvailabilityCoordinate
            or type(self.frame) is not NodeOutputFrame
            or type(self.provenance) is not ExecutionPublicationProvenance
        ):
            raise SnapshotMismatchError("publication evidence has an invalid typed frame")

    def __hash__(self) -> Never:
        raise TypeError("publication evidence is unhashable")


@dataclass(frozen=True, slots=True, eq=False)
class AdmittedGraphInput(Generic[GraphValueT]):
    coordinate: GraphInputAvailabilityCoordinate[GraphValueT]
    frame: GraphInputFrame[GraphValueT] = field(compare=False, repr=False, hash=False)

    def __hash__(self) -> Never:
        raise TypeError("scoped frame records are unhashable")


@dataclass(frozen=True, slots=True)
class ExecutionPublicationProvenance:
    execution_token: GraphExecutionToken


@dataclass(frozen=True, slots=True, eq=False)
class ConfirmedPublication(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT] = field(compare=False, repr=False, hash=False)
    acknowledged_revision: int
    provenance: ExecutionPublicationProvenance

    def __hash__(self) -> Never:
        raise TypeError("scoped frame records are unhashable")


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


def _insert_frame_record(
    records: tuple[FrameRecordT, ...],
    record: FrameRecordT,
    *,
    coordinate: Callable[[FrameRecordT], CoordinateT],
    duplicate_message: str,
) -> tuple[FrameRecordT, ...]:
    """Insert one immutable frame record in its canonical coordinate order."""

    record_coordinate = coordinate(record)
    if any(coordinate(existing) == record_coordinate for existing in records):
        raise GraphValuePublicationError(duplicate_message)
    coordinates = tuple(cast(_ComparableCoordinate, coordinate(existing)) for existing in records)
    position = bisect_left(coordinates, cast(_ComparableCoordinate, record_coordinate))
    return (*records[:position], record, *records[position:])


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
            records: tuple[FrameRecord[GraphValueT], ...] = self.graph_inputs
        elif isinstance(coordinate, PublicationAvailabilityCoordinate):
            records = self.publications
        elif isinstance(coordinate, ResumeInputAvailabilityCoordinate):
            records = self.resume_inputs
        else:
            if type(coordinate) is not ChildBoundaryAvailabilityCoordinate:
                raise SnapshotMismatchError("unsupported frame coordinate")
            records = self.child_boundaries
        for record in records:
            if record.coordinate == coordinate:
                return record
        raise SnapshotMismatchError(f"continuation has no frame at coordinate {coordinate!r}")

    def add_graph_input(
        self,
        record: AdmittedGraphInput[GraphValueT],
    ) -> ScopedFrameIndex[GraphValueT]:
        return replace(
            self,
            graph_inputs=_insert_frame_record(
                self.graph_inputs,
                record,
                coordinate=lambda item: item.coordinate,
                duplicate_message="graph input coordinate was admitted more than once",
            ),
        )

    def add_publication(
        self,
        record: ConfirmedPublication[GraphValueT],
    ) -> ScopedFrameIndex[GraphValueT]:
        return replace(
            self,
            publications=_insert_frame_record(
                self.publications,
                record,
                coordinate=lambda item: item.coordinate,
                duplicate_message="stable activation was published more than once",
            ),
        )

    def add_resume_input(
        self,
        record: AdmittedResumeInput[GraphValueT],
    ) -> ScopedFrameIndex[GraphValueT]:
        return replace(
            self,
            resume_inputs=_insert_frame_record(
                self.resume_inputs,
                record,
                coordinate=lambda item: item.coordinate,
                duplicate_message="resume input coordinate was admitted more than once",
            ),
        )

    def add_child_boundary(
        self,
        record: ConfirmedChildBoundary[GraphValueT],
    ) -> ScopedFrameIndex[GraphValueT]:
        return replace(
            self,
            child_boundaries=_insert_frame_record(
                self.child_boundaries,
                record,
                coordinate=lambda item: item.coordinate,
                duplicate_message="child boundary coordinate was confirmed more than once",
            ),
        )


@dataclass(frozen=True, slots=True, eq=False)
class _CompiledFamilyIdentity:
    pass


@dataclass(frozen=True, slots=True)
class ScopedStateBinding:
    """Bind one scoped coordinate to its authoritative graph state.

    The root and every nested run use this same binding shape.  A nested
    parent activation is projected from ``GraphRunState.parent`` so the
    binding never carries a second copy of that identity.
    """

    scope_run: ScopeRunCoordinate
    state: GraphRunState

    @property
    def parent_activation(self) -> StableActivation | None:
        """Validate and project the State-owned parent identity, if nested."""

        if self.state.run_id != self.scope_run.graph_run_id:
            raise SnapshotMismatchError("scope-run coordinate does not match its graph state")
        parent = self.state.parent
        if parent is None:
            if self.scope_run.scope:
                raise SnapshotMismatchError(
                    "nested graph state does not match its runtime scope: missing its parent activation"
                )
            return None
        if not self.scope_run.scope:
            raise SnapshotMismatchError("root graph state cannot carry a parent activation")
        return parent_activation_for_child(self.scope_run, parent)


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class _ContinuationSnapshot(Generic[GraphValueT]):
    """One immutable continuation payload with explicit provenance."""

    family_identity: _CompiledFamilyIdentity
    root_state: GraphRunState
    child_states: tuple[ScopedStateBinding, ...]
    frames: ScopedFrameIndex[GraphValueT]
    recovered: bool


ContinuationSnapshot: TypeAlias = _ContinuationSnapshot[GraphValueT]


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


def _make_continuation(
    family_identity: _CompiledFamilyIdentity,
    root_state: GraphRunState,
    child_states: tuple[ScopedStateBinding, ...],
    frames: ScopedFrameIndex[GraphValueT],
    *,
    recovered: bool,
) -> _GraphContinuation[GraphValueT]:
    snapshot = _ContinuationSnapshot(family_identity, root_state, child_states, frames, recovered)
    return _GraphContinuation(_snapshot=snapshot, _seal=_CONTINUATION_SEAL)


__all__ = [
    "_CompiledFamilyIdentity",
    "_GraphContinuation",
    "_admit_continuation",
    "_make_continuation",
]

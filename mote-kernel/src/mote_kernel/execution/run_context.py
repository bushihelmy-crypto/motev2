"""Invocation-local scoped frames and authoritative run evidence."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Generic, Never, Protocol, Self, TypeAlias, TypeVar, cast, overload

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


def require_publication_confirmation(
    acknowledged_revision: int,
    provenance: ExecutionPublicationProvenance,
) -> GraphExecutionToken:
    if (
        type(acknowledged_revision) is not int
        or acknowledged_revision < 1
        or type(provenance) is not ExecutionPublicationProvenance
    ):
        raise SnapshotMismatchError("publication has inconsistent coordinates")
    try:
        return GraphExecutionToken.admit(provenance.execution_token)
    except ValueError as error:
        raise SnapshotMismatchError("publication has inconsistent execution provenance") from error


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


@dataclass(frozen=True, slots=True)
class UncreatedGraphRun:
    """An explicit authoritative negative read, never inferred from an omitted record."""

    scope_run: ScopeRunCoordinate

    def __post_init__(self) -> None:
        if type(self.scope_run) is not ScopeRunCoordinate or not self.scope_run.scope:
            raise SnapshotMismatchError("uncreated child evidence requires a nested scope-run coordinate")
        replace(self.scope_run)


ScopedRunEvidence: TypeAlias = ScopedStateBinding | UncreatedGraphRun


__all__: list[str] = []

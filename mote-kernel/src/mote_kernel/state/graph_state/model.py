"""Authoritative, recoverable graph-run state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType, Self

from mote_kernel.state.graph_state.frontier_model import GraphFrontierState, GraphResumeInputCodec
from mote_kernel.state.graph_state.identity import (
    ActivationReference,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphJoinOccurrenceIdentity,
    GraphRouteId,
    GraphRunId,
    is_canonical_identity,
)
from mote_kernel.state.graph_state.resource_model import ResourceSnapshot

GraphAbortReason = NewType("GraphAbortReason", str)


@dataclass(frozen=True, slots=True, order=True)
class GraphConfigCursor:
    """The immutable Config snapshot coordinate carried by graph state.

    Graph definition identity and Config identity are deliberately separate.
    A running graph may consume Config ``N + 1`` without changing its compiled
    topology, so the cursor belongs to the authoritative state snapshot rather
    than to the graph definition fields.
    """

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    revision: int
    digest: str | None = None

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.definition_id):
            raise ValueError("config definition identity must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ValueError("config definition version must be positive")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("config revision must be positive")
        if self.digest is not None and (
            type(self.digest) is not str or not self.digest or self.digest != self.digest.strip()
        ):
            raise ValueError("config digest must be a non-empty string when present")

    @classmethod
    def admit(cls, cursor: GraphConfigCursor, /) -> Self:
        """Revalidate a cursor received across a command or value boundary."""

        if type(cursor) is not cls:
            raise TypeError("Config cursor is malformed")
        try:
            return cls(
                cursor.definition_id,
                cursor.definition_version,
                cursor.revision,
                cursor.digest,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise TypeError("Config cursor is malformed") from error

    def transition_to(self, candidate: GraphConfigCursor, /) -> Self:
        """Apply the monotonic Config revision rule at the cursor owner."""

        candidate = type(self).admit(candidate)
        if candidate.definition_id != self.definition_id or candidate.definition_version != self.definition_version:
            raise ValueError("node settlement cannot change the Config identity or version")
        if candidate.revision == self.revision:
            if self.digest is not None and candidate.digest is not None and candidate.digest != self.digest:
                raise ValueError("node settlement Config digest conflicts at the same revision")
            if self.digest is None and candidate.digest is not None:
                return candidate
            return self
        if candidate.revision == self.revision + 1:
            if candidate.digest is None:
                raise ValueError("a successor Config cursor requires its snapshot digest")
            return candidate
        if candidate.revision < self.revision:
            raise ValueError("node settlement Config revision cannot move backwards")
        raise ValueError("node settlement Config revision must advance exactly once")


class GraphRunStatus(Enum):
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    ABORTED = auto()


@dataclass(frozen=True, slots=True)
class GraphAbort:
    reason: GraphAbortReason


@dataclass(frozen=True, slots=True)
class GraphExecutionToken:
    generation: int
    attempt_id: GraphExecutionAttemptId


@dataclass(frozen=True, slots=True)
class GraphExecutionLease:
    token: GraphExecutionToken


@dataclass(frozen=True, slots=True)
class GraphJoinProgress:
    occurrence: GraphJoinOccurrenceIdentity
    arrived: tuple[ActivationReference, ...]


@dataclass(frozen=True, slots=True)
class GraphRunState:
    run_id: GraphRunId
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    status: GraphRunStatus
    superstep: int
    frontier: GraphFrontierState
    execution_sequence: int = 0
    resume_input_codec: GraphResumeInputCodec | None = None
    join_progress: tuple[GraphJoinProgress, ...] = ()
    # One canonical success reference per committed activation.  Causes and
    # Join progress may only point at entries in this ledger; keeping it in the
    # sole runtime snapshot makes recovery admission deterministic.
    settled_activations: tuple[ActivationReference, ...] = ()
    resources: ResourceSnapshot | None = None
    execution: GraphExecutionLease | None = None
    abort: GraphAbort | None = None
    parent: GraphActivationIdentity | None = None
    revision: int = 0
    # The terminal route selected by the last completed frontier.  It is
    # retained after the canonical completed state clears the frontier so a
    # parent nested graph can route from the child's returned value.
    completion_route: GraphRouteId | None = None
    # Exact configuration snapshot revision active at this graph boundary.
    # The graph definition identity/version above remains topology identity;
    # this independent cursor lets a running graph move from Config N to N+1
    # without creating a new definition or run.
    config_revision: int = 1
    # These fields are intentionally separate from ``definition_id`` and
    # ``definition_version``.  An omitted identity means the initial Config
    # uses the topology identity; ``__post_init__`` materializes that default
    # immediately so later ``replace`` calls preserve the independent cursor.
    config_definition_id: GraphDefinitionId | None = None
    config_definition_version: GraphDefinitionVersion | None = None
    config_digest: str | None = None

    def __post_init__(self) -> None:
        if self.config_definition_id is None:
            object.__setattr__(self, "config_definition_id", self.definition_id)
        if self.config_definition_version is None:
            object.__setattr__(self, "config_definition_version", self.definition_version)

    @property
    def config_cursor(self) -> GraphConfigCursor:
        """Return the exact Config coordinate represented by this state."""

        definition_id = self.config_definition_id
        definition_version = self.config_definition_version
        if definition_id is None or definition_version is None:
            raise ValueError("graph state is missing its Config identity")
        return GraphConfigCursor(definition_id, definition_version, self.config_revision, self.config_digest)


__all__ = [
    "GraphAbort",
    "GraphAbortReason",
    "GraphConfigCursor",
    "GraphExecutionLease",
    "GraphExecutionToken",
    "GraphJoinProgress",
    "GraphRunState",
    "GraphRunStatus",
]

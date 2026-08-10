"""Typed graph construction and execution errors."""


class ExecutionError(Exception):
    """Base class for errors owned by the graph execution substrate."""


class PlanningError(ExecutionError):
    """A committed execution projection cannot be planned safely."""


class SnapshotMismatchError(PlanningError):
    """A snapshot does not belong to the compiled graph being planned."""


class InvalidExecutionSnapshotError(PlanningError):
    """A snapshot contains an invalid or unknown execution position."""


class ExecutionLimitError(PlanningError):
    """Planning would exceed an explicit graph execution limit."""


class GraphValidationError(ExecutionError):
    """A graph definition violates a static topology invariant."""


class DuplicateNodeError(GraphValidationError):
    """A graph definition contains the same node identity more than once."""


class DuplicateBoundaryError(GraphValidationError):
    """A graph definition repeats an entry or exit identity."""


class DuplicateEdgeError(GraphValidationError):
    """A graph definition repeats the same static edge."""


class DuplicateGraphDefinitionError(GraphValidationError):
    """One graph tree binds a definition identity and version more than once."""


class RecursiveGraphDefinitionError(GraphValidationError):
    """Nested graph definitions recursively contain a graph still being validated."""


class UnknownNodeError(GraphValidationError):
    """An edge or graph boundary references an unknown node."""


class MissingEntryError(GraphValidationError):
    """A graph definition has no entry nodes."""


class UnreachableNodeError(GraphValidationError):
    """A graph definition contains a node unreachable from every entry."""


class InvalidJoinError(GraphValidationError):
    """A join edge has an invalid or ambiguous source set."""


class InvalidGraphIdentityError(GraphValidationError):
    """A graph, node, or route identity is empty or not trimmed."""


__all__ = [
    "DuplicateBoundaryError",
    "DuplicateEdgeError",
    "DuplicateGraphDefinitionError",
    "DuplicateNodeError",
    "ExecutionError",
    "ExecutionLimitError",
    "GraphValidationError",
    "InvalidExecutionSnapshotError",
    "InvalidGraphIdentityError",
    "InvalidJoinError",
    "MissingEntryError",
    "PlanningError",
    "RecursiveGraphDefinitionError",
    "SnapshotMismatchError",
    "UnknownNodeError",
    "UnreachableNodeError",
]

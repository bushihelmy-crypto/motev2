"""Typed graph construction and execution errors."""


class ExecutionError(Exception):
    """Base class for errors owned by the graph execution substrate."""


class GraphValidationError(ExecutionError):
    """A graph definition violates a static topology invariant."""


class DuplicateNodeError(GraphValidationError):
    """A graph definition contains the same node identity more than once."""


class UnknownNodeError(GraphValidationError):
    """An edge or graph boundary references an unknown node."""


class MissingEntryError(GraphValidationError):
    """A graph definition has no entry nodes."""


class UnreachableNodeError(GraphValidationError):
    """A graph definition contains a node unreachable from every entry."""


class InvalidJoinError(GraphValidationError):
    """A join edge has an invalid or ambiguous source set."""


class InvalidGraphIdentityError(GraphValidationError):
    """A graph, node, or route identity is empty or reserved."""


__all__ = [
    "DuplicateNodeError",
    "ExecutionError",
    "GraphValidationError",
    "InvalidGraphIdentityError",
    "InvalidJoinError",
    "MissingEntryError",
    "UnknownNodeError",
    "UnreachableNodeError",
]

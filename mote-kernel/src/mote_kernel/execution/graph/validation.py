"""Static graph validation."""

from collections import deque
from typing import TypeVar

from mote_kernel.execution.errors import (
    DuplicateNodeError,
    InvalidGraphIdentityError,
    InvalidJoinError,
    MissingEntryError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import NodeId

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _require_identity(value: str, *, kind: str) -> None:
    if not value or value.strip() != value:
        raise InvalidGraphIdentityError(f"{kind} identity must be non-empty and trimmed: {value!r}")


def _validate_node_references(definition: GraphDefinition[InputT, OutputT], node_ids: frozenset[NodeId]) -> None:
    for entry in definition.entries:
        if entry not in node_ids:
            raise UnknownNodeError(f"entry references unknown node: {entry}")
    for exit_node in definition.exits:
        if exit_node not in node_ids:
            raise UnknownNodeError(f"exit references unknown node: {exit_node}")

    for edge in definition.edges:
        if isinstance(edge, DirectEdge | ConditionalEdge):
            referenced = (edge.source, edge.target)
        else:
            referenced = (*edge.sources, edge.target)
        for node_id in referenced:
            if node_id not in node_ids:
                raise UnknownNodeError(f"edge references unknown node: {node_id}")


def _validate_joins(definition: GraphDefinition[InputT, OutputT]) -> None:
    seen: set[tuple[frozenset[NodeId], NodeId]] = set()
    for edge in definition.edges:
        if not isinstance(edge, JoinEdge):
            continue
        sources = frozenset(edge.sources)
        if len(edge.sources) < 2 or len(sources) != len(edge.sources) or edge.target in sources:
            raise InvalidJoinError(f"invalid join into node: {edge.target}")
        identity = (sources, edge.target)
        if identity in seen:
            raise InvalidJoinError(f"duplicate join into node: {edge.target}")
        seen.add(identity)


def _reachable_nodes(definition: GraphDefinition[InputT, OutputT]) -> frozenset[NodeId]:
    outgoing: dict[NodeId, set[NodeId]] = {node.node_id: set() for node in definition.nodes}
    for edge in definition.edges:
        if isinstance(edge, DirectEdge | ConditionalEdge):
            outgoing[edge.source].add(edge.target)
        else:
            for source in edge.sources:
                outgoing[source].add(edge.target)

    pending = deque(definition.entries)
    reachable: set[NodeId] = set()
    while pending:
        node_id = pending.popleft()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(sorted(outgoing[node_id]))
    return frozenset(reachable)


def validate_graph(definition: GraphDefinition[InputT, OutputT]) -> None:
    """Reject a graph definition that violates static execution invariants."""

    _require_identity(definition.definition_id, kind="graph")
    if definition.version < 1:
        raise InvalidGraphIdentityError("graph definition version must be positive")
    if not definition.entries:
        raise MissingEntryError("graph definition requires at least one entry")

    node_ids = tuple(node.node_id for node in definition.nodes)
    for node_id in node_ids:
        _require_identity(node_id, kind="node")
    if len(frozenset(node_ids)) != len(node_ids):
        raise DuplicateNodeError("graph definition contains duplicate node identities")
    for edge in definition.edges:
        if isinstance(edge, ConditionalEdge):
            _require_identity(edge.route, kind="route")

    known_nodes = frozenset(node_ids)
    _validate_node_references(definition, known_nodes)
    _validate_joins(definition)
    unreachable = known_nodes - _reachable_nodes(definition)
    if unreachable:
        raise UnreachableNodeError(f"unreachable nodes: {', '.join(sorted(unreachable))}")


__all__ = ["validate_graph"]

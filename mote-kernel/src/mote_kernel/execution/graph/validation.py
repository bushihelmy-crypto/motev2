"""Static graph validation."""

from collections import deque
from enum import Enum, auto
from typing import TypeVar

from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    DuplicateEdgeError,
    DuplicateGraphDefinitionError,
    DuplicateNodeError,
    InvalidGraphIdentityError,
    InvalidJoinError,
    InvalidResourceDefinitionError,
    MissingEntryError,
    RecursiveGraphDefinitionError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph.constants import END, START
from mote_kernel.execution.graph.definition import (
    GraphDefinition,
    NestedGraphNodeDefinition,
)
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import NodeDefinition
from mote_kernel.state.graph_state.identity import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class _ValidationStatus(Enum):
    VISITING = auto()
    VALIDATED = auto()


class _DefinitionVisit:
    __slots__ = ("definition_object_id", "status")

    def __init__(self, definition_object_id: int, status: _ValidationStatus) -> None:
        self.definition_object_id = definition_object_id
        self.status = status


def _require_identity(value: str, *, kind: str) -> None:
    if not value or value.strip() != value or "\n" in value or "\r" in value:
        raise InvalidGraphIdentityError(f"{kind} identity must be non-empty and trimmed: {value!r}")


def _validate_resume_input(definition: GraphDefinition[InputT, OutputT]) -> None:
    binding = definition.resume_input
    if binding is None:
        return
    _require_identity(binding.codec_id, kind="resume input codec")
    if binding.version < 1:
        raise InvalidGraphIdentityError("resume input codec version must be positive")


def _validate_node_references(definition: GraphDefinition[InputT, OutputT], node_ids: frozenset[GraphNodeId]) -> None:
    for entry in definition.entries:
        if entry not in node_ids:
            raise UnknownNodeError(f"entry references unknown node: {entry}")
    for edge in definition.edges:
        if isinstance(edge, DirectEdge | ConditionalEdge):
            referenced = (edge.source,) if edge.target == END else (edge.source, edge.target)
        else:
            referenced = edge.sources if edge.target == END else (*edge.sources, edge.target)
        for node_id in referenced:
            if node_id not in node_ids:
                raise UnknownNodeError(f"edge references unknown node: {node_id}")


def _validate_joins(definition: GraphDefinition[InputT, OutputT]) -> None:
    seen: set[tuple[frozenset[GraphNodeId], GraphNodeId]] = set()
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


def _validate_duplicates(definition: GraphDefinition[InputT, OutputT]) -> None:
    if len(frozenset(definition.entries)) != len(definition.entries):
        raise DuplicateBoundaryError("graph definition contains duplicate entries")
    direct_edges: set[DirectEdge] = set()
    conditional_routes: set[tuple[GraphNodeId, str]] = set()
    for edge in definition.edges:
        if isinstance(edge, DirectEdge):
            if edge in direct_edges:
                raise DuplicateEdgeError(f"duplicate direct edge: {edge.source} -> {edge.target}")
            direct_edges.add(edge)
        elif isinstance(edge, ConditionalEdge):
            route = (edge.source, edge.route)
            if route in conditional_routes:
                raise DuplicateEdgeError(f"duplicate conditional route {edge.route!r} from node {edge.source!r}")
            conditional_routes.add(route)


def _validate_nested_graphs(
    definition: GraphDefinition[InputT, OutputT],
    definitions: dict[tuple[GraphDefinitionId, GraphDefinitionVersion], _DefinitionVisit],
) -> None:
    for node in definition.nodes:
        if isinstance(node, NestedGraphNodeDefinition):
            _validate_graph(node.graph, definitions)


def _validate_resources(definition: GraphDefinition[InputT, OutputT]) -> None:
    resource_ids = tuple(resource.resource_id for resource in definition.resources)
    resource_orders = tuple(resource.order for resource in definition.resources)
    for resource_id in resource_ids:
        try:
            _require_identity(resource_id, kind="resource")
        except InvalidGraphIdentityError as error:
            raise InvalidResourceDefinitionError(str(error)) from error
    if len(frozenset(resource_ids)) != len(resource_ids):
        raise InvalidResourceDefinitionError("graph definition contains duplicate resource identities")
    if any(order < 0 for order in resource_orders):
        raise InvalidResourceDefinitionError("resource order must be non-negative")
    if len(frozenset(resource_orders)) != len(resource_orders):
        raise InvalidResourceDefinitionError("graph resources must have distinct order values")

    known_resources = frozenset(resource_ids)
    for node in definition.nodes:
        if not isinstance(node, NodeDefinition):
            continue
        if len(frozenset(node.resources)) != len(node.resources):
            raise InvalidResourceDefinitionError(f"node {node.node_id!r} repeats a resource requirement")
        unknown = frozenset(node.resources) - known_resources
        if unknown:
            raise InvalidResourceDefinitionError(
                f"node {node.node_id!r} references unknown resources: {tuple(sorted(unknown))!r}"
            )


def _reachable_nodes(definition: GraphDefinition[InputT, OutputT]) -> frozenset[GraphNodeId]:
    outgoing: dict[GraphNodeId, set[GraphNodeId]] = {node.node_id: set() for node in definition.nodes}
    joins: list[JoinEdge] = []
    for edge in definition.edges:
        if isinstance(edge, DirectEdge | ConditionalEdge):
            if edge.target != END:
                outgoing[edge.source].add(edge.target)
        else:
            joins.append(edge)

    pending = deque(definition.entries)
    reachable: set[GraphNodeId] = set()
    while pending or any(
        edge.target != END and set(edge.sources) <= reachable and edge.target not in reachable for edge in joins
    ):
        while pending:
            node_id = pending.popleft()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            pending.extend(sorted(outgoing[node_id]))
        pending.extend(
            sorted(
                edge.target
                for edge in joins
                if edge.target != END and set(edge.sources) <= reachable and edge.target not in reachable
            )
        )
    return frozenset(reachable)


def _validate_graph(
    definition: GraphDefinition[InputT, OutputT],
    definitions: dict[tuple[GraphDefinitionId, GraphDefinitionVersion], _DefinitionVisit],
) -> None:
    _require_identity(definition.definition_id, kind="graph")
    if definition.version < 1:
        raise InvalidGraphIdentityError("graph definition version must be positive")
    definition_key = (definition.definition_id, definition.version)
    existing = definitions.get(definition_key)
    if existing is not None:
        if existing.definition_object_id != id(definition):
            raise DuplicateGraphDefinitionError(
                f"graph definition identity collision: {definition.definition_id}@{definition.version}"
            )
        if existing.status is _ValidationStatus.VISITING:
            raise RecursiveGraphDefinitionError(
                f"recursive graph definition: {definition.definition_id}@{definition.version}"
            )
        return
    definitions[definition_key] = _DefinitionVisit(id(definition), _ValidationStatus.VISITING)
    if not definition.entries:
        raise MissingEntryError("graph definition requires at least one entry")

    node_ids = tuple(node.node_id for node in definition.nodes)
    for node_id in node_ids:
        _require_identity(node_id, kind="node")
        if node_id in (START, END):
            raise InvalidGraphIdentityError("START and END are virtual boundaries and cannot be declared as nodes")
    if len(frozenset(node_ids)) != len(node_ids):
        raise DuplicateNodeError("graph definition contains duplicate node identities")
    for edge in definition.edges:
        if isinstance(edge, ConditionalEdge):
            _require_identity(edge.route, kind="route")

    known_nodes = frozenset(node_ids)
    _validate_resources(definition)
    _validate_resume_input(definition)
    _validate_duplicates(definition)
    _validate_node_references(definition, known_nodes)
    _validate_joins(definition)
    _validate_nested_graphs(definition, definitions)
    unreachable = known_nodes - _reachable_nodes(definition)
    if unreachable:
        raise UnreachableNodeError(f"unreachable nodes: {', '.join(sorted(unreachable))}")
    definitions[definition_key].status = _ValidationStatus.VALIDATED


def validate_graph(definition: GraphDefinition[InputT, OutputT]) -> None:
    """Reject a graph definition that violates static execution invariants."""

    _validate_graph(definition, {})


__all__ = ["validate_graph"]

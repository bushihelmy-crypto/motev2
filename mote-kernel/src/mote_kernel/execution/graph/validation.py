"""Static validation of complete immutable graph definitions."""

from dataclasses import dataclass
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
    RecursiveGraphDefinitionError,
    UnknownNodeError,
)
from mote_kernel.execution.graph.constants import END, START
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId

GraphValueT = TypeVar("GraphValueT")


class _ValidationStatus(Enum):
    VISITING = auto()
    VALIDATED = auto()


@dataclass(slots=True)
class _DefinitionVisit:
    definition_object_id: int
    status: _ValidationStatus


def require_graph_identity(value: str, *, kind: str) -> None:
    if type(value) is not str or not value or value.strip() != value or "\n" in value or "\r" in value:
        raise InvalidGraphIdentityError(f"{kind} identity must be a non-empty trimmed string")


def _validate_resources(definition: GraphDefinition[GraphValueT]) -> None:
    resource_ids = tuple(resource.resource_id for resource in definition.resources)
    orders = tuple(resource.order for resource in definition.resources)
    if len(resource_ids) != len(set(resource_ids)) or orders != tuple(range(len(orders))):
        raise InvalidResourceDefinitionError("graph resources require unique contiguous first-seen order")
    for resource_id in resource_ids:
        try:
            require_graph_identity(resource_id, kind="resource")
        except InvalidGraphIdentityError as error:
            raise InvalidResourceDefinitionError(str(error)) from error
    known = frozenset(resource_ids)
    for node in definition.nodes:
        if not isinstance(node, CallableNodeDefinition):
            continue
        if len(node.resources) != len(set(node.resources)):
            raise InvalidResourceDefinitionError(f"node {node.node_id!r} repeats a resource requirement")
        if not set(node.resources) <= known:
            raise InvalidResourceDefinitionError(f"node {node.node_id!r} references an unknown resource")


def _validate_edges(definition: GraphDefinition[GraphValueT], known: frozenset[GraphNodeId]) -> None:
    if len(definition.entries) != len(set(definition.entries)):
        raise DuplicateBoundaryError("graph definition contains duplicate explicit START entries")
    if any(entry not in known for entry in definition.entries):
        raise UnknownNodeError("START edge references an unknown node")
    direct_seen: set[DirectEdge] = set()
    conditional_seen: set[tuple[GraphNodeId, str]] = set()
    join_seen: set[tuple[tuple[GraphNodeId, ...], GraphNodeId]] = set()
    nested_ids = frozenset(node.node_id for node in definition.nodes if isinstance(node, NestedGraphNodeDefinition))
    for edge in definition.edges:
        if isinstance(edge, DirectEdge):
            if edge.source not in known or edge.target not in (*known, END):
                raise UnknownNodeError("direct edge references an unknown node")
            if edge in direct_seen:
                raise DuplicateEdgeError(f"duplicate direct edge: {edge.source} -> {edge.target}")
            direct_seen.add(edge)
            continue
        if isinstance(edge, ConditionalEdge):
            require_graph_identity(edge.route, kind="route")
            if edge.source not in known or edge.target not in (*known, END):
                raise UnknownNodeError("conditional edge references an unknown node")
            if edge.source in nested_ids:
                raise InvalidGraphIdentityError("nested graph nodes cannot be conditional routing sources")
            key = (edge.source, edge.route)
            if key in conditional_seen:
                raise DuplicateEdgeError(f"duplicate conditional route {edge.route!r} from {edge.source!r}")
            conditional_seen.add(key)
            continue
        sources = tuple(sorted(edge.sources))
        key = (sources, edge.target)
        if (
            len(sources) < 2
            or len(sources) != len(set(sources))
            or not set(sources) <= known
            or edge.target not in (*known, END)
            or edge.target in sources
        ):
            raise InvalidJoinError(f"invalid join into node: {edge.target}")
        if key in join_seen:
            raise InvalidJoinError(f"duplicate join into node: {edge.target}")
        join_seen.add(key)


def _validate_definition(
    definition: GraphDefinition[GraphValueT],
    visits: dict[tuple[GraphDefinitionId, GraphDefinitionVersion], _DefinitionVisit],
) -> None:
    require_graph_identity(definition.definition_id, kind="graph")
    if type(definition.version) is not int or definition.version < 1:
        raise InvalidGraphIdentityError("graph definition version must be a positive integer")
    key = (definition.definition_id, definition.version)
    existing = visits.get(key)
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
    visits[key] = _DefinitionVisit(id(definition), _ValidationStatus.VISITING)
    node_ids = tuple(node.node_id for node in definition.nodes)
    for node_id in node_ids:
        require_graph_identity(node_id, kind="node")
        if node_id in (START, END):
            raise InvalidGraphIdentityError("START and END cannot be concrete graph nodes")
    if len(node_ids) != len(set(node_ids)):
        raise DuplicateNodeError("graph definition contains duplicate node identities")
    known = frozenset(node_ids)
    _validate_resources(definition)
    _validate_edges(definition, known)
    binding = definition.resume_input
    if binding is not None:
        require_graph_identity(binding.codec_id, kind="resume input codec")
        if type(binding.version) is not int or binding.version < 1:
            raise InvalidGraphIdentityError("resume input codec version must be a positive integer")
    for node in definition.nodes:
        if isinstance(node, NestedGraphNodeDefinition):
            _validate_definition(node.graph, visits)
    visits[key].status = _ValidationStatus.VALIDATED


def validate_graph(definition: GraphDefinition[GraphValueT]) -> None:
    """Reject malformed identities, resources, edges, and graph-family recursion."""

    _validate_definition(definition, {})


__all__: list[str] = []

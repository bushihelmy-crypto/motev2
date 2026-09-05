"""Canonical graph port declarations and compiled binding identities."""

import operator
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, auto
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.errors import ExecutionError, GraphValidationError
from mote_kernel.state.graph_state import GraphNodeId, GraphRouteId
from mote_kernel.state.graph_state.identity import is_canonical_identity

GraphValueT = TypeVar("GraphValueT")
GraphValueT_co = TypeVar("GraphValueT_co", covariant=True)
ValueT = TypeVar("ValueT")
DefinitionScope: TypeAlias = tuple[GraphNodeId, ...]


def canonical_port_name(name: str, *, kind: str = "port") -> str:
    """Return one stable user-facing port name or fail at its call boundary."""

    if not is_canonical_identity(name):
        raise GraphValidationError(f"{kind} name must be a non-empty trimmed string")
    return name


def canonical_nominal_type(value_type: type[ValueT] | str) -> "NominalTypeDescriptor[ValueT]":
    """Normalize a concrete class usable by exact runtime admission."""

    if not isinstance(value_type, type):
        raise GraphValidationError("port type must be one concrete nominal class")
    if value_type is object or operator.is_(value_type, typing.Any):
        raise GraphValidationError("port type must be one concrete nominal class")
    return NominalTypeDescriptor(value_type)


@dataclass(frozen=True, slots=True)
class NominalTypeDescriptor(Generic[GraphValueT_co]):
    value_type: type[GraphValueT_co]


@dataclass(frozen=True, slots=True)
class GraphInputRef(Generic[GraphValueT_co]):
    name: str
    descriptor: NominalTypeDescriptor[GraphValueT_co]


@dataclass(frozen=True, slots=True)
class NodeOutputRef:
    node_id: GraphNodeId
    output_name: str


@dataclass(frozen=True, slots=True)
class PredecessorOutputRef:
    """An output port supplied by the activation's one actual predecessor."""

    output_name: str


ValueSourceRef: TypeAlias = GraphInputRef[GraphValueT] | NodeOutputRef
InputBindingSource: TypeAlias = ValueSourceRef[GraphValueT] | PredecessorOutputRef


@dataclass(frozen=True, slots=True, order=True)
class GraphInputPort:
    definition_scope: DefinitionScope
    name: str


@dataclass(frozen=True, slots=True, order=True)
class NodeInputPort:
    definition_scope: DefinitionScope
    node_id: GraphNodeId
    local_name: str


@dataclass(frozen=True, slots=True, order=True)
class NodeOutputPort:
    definition_scope: DefinitionScope
    node_id: GraphNodeId
    output_name: str


@dataclass(frozen=True, slots=True, order=True)
class GraphOutputPort:
    definition_scope: DefinitionScope
    boundary_name: str


ResolvedValueSource: TypeAlias = GraphInputPort | NodeOutputPort

# An activation gate is a tuple of source nodes and the route domain each
# source may contribute.  It lives with the port declarations so input
# materialization and the topology plan share one gate shape without an import
# cycle.
ActivationGateSource: TypeAlias = tuple[GraphNodeId, frozenset[GraphRouteId | None]]
ActivationGate: TypeAlias = tuple[ActivationGateSource, ...]


@dataclass(frozen=True, slots=True)
class InputBinding(Generic[GraphValueT]):
    local_name: str
    source: InputBindingSource[GraphValueT]


@dataclass(frozen=True, slots=True)
class InputBindings(Generic[GraphValueT]):
    entries: tuple[InputBinding[GraphValueT], ...]


@dataclass(frozen=True, slots=True)
class OutputDeclaration(Generic[GraphValueT_co]):
    name: str
    descriptor: NominalTypeDescriptor[GraphValueT_co]


@dataclass(frozen=True, slots=True)
class OutputDeclarations(Generic[GraphValueT]):
    entries: tuple[OutputDeclaration[GraphValueT], ...]


@dataclass(frozen=True, slots=True)
class GraphOutputDeclaration(Generic[GraphValueT]):
    boundary_name: str
    source: ValueSourceRef[GraphValueT]


@dataclass(frozen=True, slots=True)
class GraphOutputDeclarations(Generic[GraphValueT]):
    entries: tuple[GraphOutputDeclaration[GraphValueT], ...]


class PublicationSelectionKind(IntEnum):
    ABSOLUTE = auto()
    RELATIVE = auto()


@dataclass(frozen=True, slots=True)
class PublicationSelection:
    kind: PublicationSelectionKind
    superstep: int

    def resolve(self, anchor_superstep: int) -> int:
        selected = (
            self.superstep if self.kind is PublicationSelectionKind.ABSOLUTE else anchor_superstep - self.superstep
        )
        if selected < 0:
            raise GraphValidationError("publication selection precedes the graph run")
        return selected


@dataclass(frozen=True, slots=True)
class CompiledPredecessorInput:
    """Compiler-proved output ports for one control-causal input."""

    target: GraphNodeId
    input_name: str
    sources: tuple[NodeOutputPort, ...]


ResolvedInputSource: TypeAlias = ResolvedValueSource | CompiledPredecessorInput


def require_publication_selection(
    selection: PublicationSelection | None,
    error: ExecutionError,
) -> PublicationSelection:
    """Return the compiler-owned node publication coordinate or raise the caller's boundary error."""

    if selection is None:
        raise error
    return selection


@dataclass(frozen=True, slots=True)
class ResolvedInputBinding(Generic[GraphValueT]):
    destination: NodeInputPort
    source: ResolvedInputSource
    descriptor: NominalTypeDescriptor[GraphValueT]
    publication: PublicationSelection | None


@dataclass(frozen=True, slots=True)
class ResolvedInputBindings(Generic[GraphValueT]):
    entries: tuple[ResolvedInputBinding[GraphValueT], ...]


@dataclass(frozen=True, slots=True)
class GraphOutputBinding(Generic[GraphValueT]):
    destination: GraphOutputPort
    source: ResolvedValueSource
    descriptor: NominalTypeDescriptor[GraphValueT]
    publication: PublicationSelection | None


@dataclass(frozen=True, slots=True)
class GraphOutputBindings(Generic[GraphValueT]):
    entries: tuple[GraphOutputBinding[GraphValueT], ...]


class FrameKind(IntEnum):
    GRAPH_INPUT = auto()
    NODE_INPUT = auto()
    NODE_OUTPUT = auto()
    GRAPH_OUTPUT = auto()


@dataclass(frozen=True, slots=True, order=True)
class FrameDescriptorIdentity:
    definition_id: str
    definition_version: int
    frame_kind: FrameKind
    owner_ordinal: int


@dataclass(frozen=True, slots=True)
class FrameDescriptor(Generic[GraphValueT]):
    identity: FrameDescriptorIdentity
    declarations: OutputDeclarations[GraphValueT]


@dataclass(frozen=True, slots=True)
class MaterializationPlan(Generic[GraphValueT]):
    bindings: ResolvedInputBindings[GraphValueT]
    descriptor: FrameDescriptor[GraphValueT]


def normalize_input_bindings(
    values: Mapping[str, InputBindingSource[GraphValueT] | type[GraphValueT]] | None,
) -> InputBindings[GraphValueT]:
    if not isinstance(values, Mapping):
        raise GraphValidationError("inputs must be a mapping")
    entries: list[InputBinding[GraphValueT]] = []
    for name, source in sorted(values.items()):
        canonical = canonical_port_name(name, kind="input")
        if not isinstance(source, GraphInputRef | NodeOutputRef | PredecessorOutputRef):
            raise GraphValidationError(f"input {canonical!r} must bind one graph input or node output")
        entries.append(InputBinding(canonical, source))
    return InputBindings(tuple(entries))


def normalize_output_declarations(
    values: Mapping[str, type[GraphValueT] | GraphInputRef[GraphValueT] | NodeOutputRef] | None,
) -> OutputDeclarations[GraphValueT]:
    if not isinstance(values, Mapping):
        raise GraphValidationError("outputs must be a mapping")
    entries: list[OutputDeclaration[GraphValueT]] = []
    for name, value_type in sorted(values.items()):
        canonical = canonical_port_name(name, kind="output")
        if isinstance(value_type, GraphInputRef | NodeOutputRef):
            raise GraphValidationError(f"output {canonical!r} must declare one concrete nominal type")
        entries.append(OutputDeclaration(canonical, canonical_nominal_type(value_type)))
    return OutputDeclarations(tuple(entries))


def normalize_graph_output_declarations(
    values: Mapping[str, GraphInputRef[GraphValueT] | NodeOutputRef | type[GraphValueT]] | None,
) -> GraphOutputDeclarations[GraphValueT]:
    if not isinstance(values, Mapping):
        raise GraphValidationError("graph outputs must be a mapping")
    entries: list[GraphOutputDeclaration[GraphValueT]] = []
    for name, source in sorted(values.items()):
        canonical = canonical_port_name(name, kind="graph output")
        if not isinstance(source, GraphInputRef | NodeOutputRef):
            raise GraphValidationError(f"graph output {canonical!r} must bind one graph input or node output")
        entries.append(GraphOutputDeclaration(canonical, source))
    return GraphOutputDeclarations(tuple(entries))


__all__: list[str] = []

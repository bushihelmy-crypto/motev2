"""Canonical immutable concrete values and execution-local frame types."""

from collections.abc import Iterator, Mapping
from dataclasses import InitVar, dataclass, field
from typing import Generic, Protocol, TypeVar, cast

from mote_kernel.config import Config, ConfigContractError, require_config
from mote_kernel.execution.errors import GraphValidationError, GraphValueAdmissionError
from mote_kernel.execution.graph.ports import (
    NominalTypeDescriptor,
    OutputDeclarations,
    canonical_nominal_type,
    canonical_port_name,
)

FactoryValueT = TypeVar("FactoryValueT")
GraphValueT = TypeVar("GraphValueT")
GraphValueT_co = TypeVar("GraphValueT_co", covariant=True)
ValueT = TypeVar("ValueT")


@dataclass(frozen=True, slots=True)
class NamedValue(Generic[GraphValueT_co]):
    name: str
    value: GraphValueT_co


class _FrameEntries(Protocol[GraphValueT_co]):
    @property
    def entries(self) -> tuple[NamedValue[GraphValueT_co], ...]: ...


FrameT = TypeVar("FrameT", bound=_FrameEntries[object])


class _ValuesSeal:
    __slots__ = ()


_VALUES_SEAL = _ValuesSeal()


def _admit_activation_config(
    activation_config: Config | None,
    *,
    error_message: str,
) -> Config | None:
    """Admit execution metadata once at the concrete frame/value owner."""

    if activation_config is None:
        return None
    try:
        require_config(activation_config)
    except ConfigContractError as error:
        raise GraphValueAdmissionError(error_message) from error
    return activation_config


def _merge_activation_config(
    inherited: Config | None,
    supplied: Config | None,
    *,
    malformed_message: str,
    conflict_message: str,
) -> Config | None:
    """Apply one explicit Config to a value while preserving its provenance."""

    if supplied is None:
        return inherited
    admitted = _admit_activation_config(supplied, error_message=malformed_message)
    if inherited is not None and inherited != admitted:
        raise GraphValueAdmissionError(conflict_message)
    return admitted


@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphValues(Generic[GraphValueT_co]):
    _entries: tuple[NamedValue[GraphValueT_co], ...] = field(repr=False)
    # The complete activation Config is execution metadata, not a named
    # business value.  Keeping it beside the values lets every nested graph
    # inherit the exact same activation without putting Config in Invocation
    # payloads or changing ordinary Graph value descriptors.
    activation_config: Config | None = field(default=None, repr=False, compare=True)
    _seal: InitVar[_ValuesSeal]

    def __post_init__(self, _seal: _ValuesSeal) -> None:
        if _seal is not _VALUES_SEAL:
            raise GraphValueAdmissionError("Graph values require their canonical owner construction")
        _admit_activation_config(
            self.activation_config,
            error_message="graph values carry a malformed activation Config",
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[str]:
        return (entry.name for entry in self._entries)

    def __getitem__(self, name: str) -> GraphValueT_co:
        canonical = canonical_port_name(name, kind="value")
        for entry in self._entries:
            if entry.name == canonical:
                return entry.value
        raise KeyError(canonical)

    def __contains__(self, name: str) -> bool:
        return any(entry.name == name for entry in self._entries)

    def keys(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self._entries)

    def values(self) -> tuple[GraphValueT_co, ...]:
        return tuple(entry.value for entry in self._entries)

    def items(self) -> tuple[tuple[str, GraphValueT_co], ...]:
        return tuple((entry.name, entry.value) for entry in self._entries)


class _FrameSeal:
    __slots__ = ()


_FRAME_SEAL = _FrameSeal()


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphInputFrame(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    activation_config: Config | None = field(default=None, repr=False, compare=True)
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("graph input frames require their canonical owner")
        _admit_activation_config(
            self.activation_config,
            error_message="graph input frame carries a malformed activation Config",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeInputFrame(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    activation_config: Config | None = field(default=None, repr=False, compare=True)
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("node input frames require their canonical owner")
        _admit_activation_config(
            self.activation_config,
            error_message="node input frame carries a malformed activation Config",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeOutputFrame(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    activation_config: Config | None = field(default=None, repr=False, compare=True)
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("node output frames require their canonical owner")
        _admit_activation_config(
            self.activation_config,
            error_message="node output frame carries a malformed activation Config",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphOutputView(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    activation_config: Config | None = field(default=None, repr=False, compare=True)
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("graph output views require their canonical owner")
        _admit_activation_config(
            self.activation_config,
            error_message="graph output view carries a malformed activation Config",
        )


def _normalize_mapping(values: Mapping[str, GraphValueT]) -> tuple[NamedValue[GraphValueT], ...]:
    return tuple(NamedValue(canonical_port_name(name, kind="value"), value) for name, value in sorted(values.items()))


def _make_graph_values(**values: FactoryValueT) -> _GraphValues[FactoryValueT]:
    entries = _normalize_mapping(values)
    return _GraphValues(_entries=entries, activation_config=None, _seal=_VALUES_SEAL)


def _make_single_graph_value(
    name: str,
    value: FactoryValueT,
    activation_config: Config | None = None,
) -> _GraphValues[FactoryValueT]:
    entry = NamedValue(canonical_port_name(name, kind="value"), value)
    activation_config = _admit_activation_config(
        activation_config,
        error_message="graph value activation Config is malformed",
    )
    return _GraphValues(
        _entries=(entry,),
        activation_config=activation_config,
        _seal=_VALUES_SEAL,
    )


def _require_graph_values(values: _GraphValues[GraphValueT]) -> _GraphValues[GraphValueT]:
    if type(values) is not _GraphValues:
        raise GraphValueAdmissionError("graph values must be produced by Graph.values()")
    names = values.keys()
    if any(
        type(name) is not str or not name or name.strip() != name or "\n" in name or "\r" in name for name in names
    ) or names != tuple(sorted(set(names))):
        raise GraphValueAdmissionError("graph values contain malformed canonical names")
    return values


def _entries_of(values: _GraphValues[GraphValueT]) -> tuple[NamedValue[GraphValueT], ...]:
    admitted = _require_graph_values(values)
    return tuple(NamedValue(name, value) for name, value in admitted.items())


def admit_exact(
    value: ValueT,
    descriptor: NominalTypeDescriptor[ValueT],
    *,
    kind: str = "value",
) -> ValueT:
    """Admit one value against its compiled nominal descriptor.

    This is the single exact-class check used by every frame wrapper and by
    the typed node adapter.  The descriptor is the runtime half of the
    statically declared port contract; no structural or subclass admission is
    performed here.
    """

    if type(descriptor) is not NominalTypeDescriptor:
        raise GraphValueAdmissionError(f"{kind} has a malformed nominal descriptor")
    try:
        canonical_nominal_type(descriptor.value_type)
    except GraphValidationError as error:
        raise GraphValueAdmissionError(f"{kind} has a malformed nominal descriptor") from error
    if type(value) is not descriptor.value_type:
        raise GraphValueAdmissionError(f"{kind} does not have its exact declared type")
    return cast(ValueT, value)


def _admit_entries(
    entries: tuple[NamedValue[GraphValueT], ...],
    declarations: OutputDeclarations[GraphValueT],
    *,
    kind: str,
) -> tuple[NamedValue[GraphValueT], ...]:
    if type(entries) is not tuple or any(type(entry) is not NamedValue for entry in entries):
        raise GraphValueAdmissionError(f"{kind} contains malformed canonical entries")
    if any(type(entry.name) is not str for entry in entries):
        raise GraphValueAdmissionError(f"{kind} contains malformed canonical names")
    expected_names = tuple(declaration.name for declaration in declarations.entries)
    actual_names = tuple(entry.name for entry in entries)
    if actual_names != expected_names:
        raise GraphValueAdmissionError(
            f"{kind} names do not match the compiled descriptor: expected {expected_names!r}, got {actual_names!r}"
        )
    for entry, declaration in zip(entries, declarations.entries, strict=True):
        admit_exact(
            entry.value,
            declaration.descriptor,
            kind=f"{kind} value for {entry.name!r}",
        )
    return entries


def _admit_frame(
    frame: FrameT,
    expected: type[FrameT],
    declarations: OutputDeclarations[GraphValueT],
    *,
    wrong_type_message: str,
    kind: str,
) -> FrameT:
    """Admit one sealed frame against its compiled declaration."""

    if type(frame) is not expected:
        raise GraphValueAdmissionError(wrong_type_message)
    _admit_entries(frame.entries, declarations, kind=kind)
    return frame


def _admit_graph_input_frame(
    frame: GraphInputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphInputFrame[GraphValueT]:
    return _admit_frame(
        frame,
        GraphInputFrame,
        declarations,
        wrong_type_message="graph input frame has the wrong nominal type",
        kind="graph input",
    )


def _admit_node_input_frame(
    frame: NodeInputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeInputFrame[GraphValueT]:
    return _admit_frame(
        frame,
        NodeInputFrame,
        declarations,
        wrong_type_message="node input frame has the wrong nominal type",
        kind="node input",
    )


def _admit_node_output_frame(
    frame: NodeOutputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeOutputFrame[GraphValueT]:
    return _admit_frame(
        frame,
        NodeOutputFrame,
        declarations,
        wrong_type_message="node output frame has the wrong nominal type",
        kind="node output",
    )


def _admit_graph_output_view(
    frame: GraphOutputView[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphOutputView[GraphValueT]:
    return _admit_frame(
        frame,
        GraphOutputView,
        declarations,
        wrong_type_message="graph output view has the wrong nominal type",
        kind="graph output",
    )


def _make_graph_input_frame(
    values: _GraphValues[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
    activation_config: Config | None = None,
) -> GraphInputFrame[GraphValueT]:
    entries = _admit_entries(_entries_of(values), declarations, kind="graph input")
    inherited = _merge_activation_config(
        values.activation_config,
        activation_config,
        malformed_message="graph input activation Config is malformed",
        conflict_message="graph input values and activation Config disagree",
    )
    return GraphInputFrame(entries=entries, activation_config=inherited, _seal=_FRAME_SEAL)


def _graph_input_from_node_input(
    frame: NodeInputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphInputFrame[GraphValueT]:
    entries = _admit_entries(frame.entries, declarations, kind="nested graph input")
    return GraphInputFrame(entries=entries, activation_config=frame.activation_config, _seal=_FRAME_SEAL)


def _make_node_input_frame(
    entries: tuple[NamedValue[GraphValueT], ...],
    declarations: OutputDeclarations[GraphValueT],
    activation_config: Config | None = None,
) -> NodeInputFrame[GraphValueT]:
    admitted = _admit_entries(entries, declarations, kind="node input")
    inherited = _admit_activation_config(
        activation_config,
        error_message="node input activation Config is malformed",
    )
    return NodeInputFrame(entries=admitted, activation_config=inherited, _seal=_FRAME_SEAL)


def _make_node_output_frame(
    values: _GraphValues[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
    activation_config: Config | None = None,
) -> NodeOutputFrame[GraphValueT]:
    entries = _admit_entries(_entries_of(values), declarations, kind="node output")
    inherited = _merge_activation_config(
        values.activation_config,
        activation_config,
        malformed_message="node output activation Config is malformed",
        conflict_message="node output values and activation Config disagree",
    )
    return NodeOutputFrame(entries=entries, activation_config=inherited, _seal=_FRAME_SEAL)


def _node_output_from_view(
    view: GraphOutputView[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeOutputFrame[GraphValueT]:
    entries = _admit_entries(view.entries, declarations, kind="nested node output")
    return NodeOutputFrame(entries=entries, activation_config=view.activation_config, _seal=_FRAME_SEAL)


def _make_graph_output_view(
    entries: tuple[NamedValue[GraphValueT], ...],
    declarations: OutputDeclarations[GraphValueT],
    activation_config: Config | None = None,
) -> GraphOutputView[GraphValueT]:
    admitted = _admit_entries(entries, declarations, kind="graph output")
    inherited = _admit_activation_config(
        activation_config,
        error_message="graph output activation Config is malformed",
    )
    return GraphOutputView(entries=admitted, activation_config=inherited, _seal=_FRAME_SEAL)


def _public_values(
    frame: NodeInputFrame[GraphValueT] | NodeOutputFrame[GraphValueT] | GraphOutputView[GraphValueT],
) -> _GraphValues[GraphValueT]:
    return _GraphValues(_entries=frame.entries, activation_config=frame.activation_config, _seal=_VALUES_SEAL)


def _frame_value(
    frame: GraphInputFrame[GraphValueT]
    | NodeInputFrame[GraphValueT]
    | NodeOutputFrame[GraphValueT]
    | GraphOutputView[GraphValueT],
    name: str,
) -> GraphValueT:
    for entry in frame.entries:
        if entry.name == name:
            return entry.value
    raise GraphValueAdmissionError(f"compiled frame does not contain value {name!r}")


def _frame_value_typed(
    frame: NodeInputFrame[GraphValueT],
    name: str,
    descriptor: NominalTypeDescriptor[ValueT],
) -> ValueT:
    """Materialize one declared node input with its exact port-local type."""

    return admit_exact(
        cast(ValueT, _frame_value(frame, name)),
        descriptor,
        kind=f"node input value for {name!r}",
    )


__all__ = [
    "_GraphValues",
    "_admit_graph_input_frame",
    "_admit_graph_output_view",
    "_admit_node_input_frame",
    "_admit_node_output_frame",
    "_frame_value",
    "_frame_value_typed",
    "_graph_input_from_node_input",
    "_make_graph_input_frame",
    "_make_graph_output_view",
    "_make_graph_values",
    "_make_node_input_frame",
    "_make_node_output_frame",
    "_make_single_graph_value",
    "_node_output_from_view",
    "_public_values",
    "_require_graph_values",
    "admit_exact",
]

"""Canonical immutable concrete values and execution-local frame types."""

from collections.abc import Iterator, Mapping
from dataclasses import InitVar, dataclass, field
from typing import Generic, TypeVar

from mote_kernel.execution.errors import GraphValueAdmissionError
from mote_kernel.execution.graph.ports import OutputDeclarations, canonical_port_name

FactoryValueT = TypeVar("FactoryValueT")
GraphValueT = TypeVar("GraphValueT")
GraphValueT_co = TypeVar("GraphValueT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class NamedValue(Generic[GraphValueT_co]):
    name: str
    value: GraphValueT_co


class _ValuesSeal:
    __slots__ = ()


_VALUES_SEAL = _ValuesSeal()


@dataclass(frozen=True, slots=True, kw_only=True)
class _ValuesConstruction(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    _seal: InitVar[_ValuesSeal]

    def __post_init__(self, _seal: _ValuesSeal) -> None:
        if _seal is not _VALUES_SEAL:
            raise GraphValueAdmissionError("Graph values require their canonical owner construction")


@dataclass(frozen=True, slots=True, kw_only=True)
class _GraphValues(Generic[GraphValueT_co]):
    _construction: InitVar[_ValuesConstruction[GraphValueT_co]]
    _seal: InitVar[_ValuesSeal]
    _entries: tuple[NamedValue[GraphValueT_co], ...] = field(init=False, repr=False)

    def __post_init__(
        self,
        _construction: _ValuesConstruction[GraphValueT_co],
        _seal: _ValuesSeal,
    ) -> None:
        if _seal is not _VALUES_SEAL:
            raise GraphValueAdmissionError("Graph values require their canonical owner construction")
        object.__setattr__(self, "_entries", _construction.entries)

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
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("graph input frames require their canonical owner")


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeInputFrame(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("node input frames require their canonical owner")


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeOutputFrame(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("node output frames require their canonical owner")


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphOutputView(Generic[GraphValueT_co]):
    entries: tuple[NamedValue[GraphValueT_co], ...]
    _seal: InitVar[_FrameSeal]

    def __post_init__(self, _seal: _FrameSeal) -> None:
        if _seal is not _FRAME_SEAL:
            raise GraphValueAdmissionError("graph output views require their canonical owner")


def _normalize_mapping(values: Mapping[str, GraphValueT]) -> tuple[NamedValue[GraphValueT], ...]:
    return tuple(NamedValue(canonical_port_name(name, kind="value"), value) for name, value in sorted(values.items()))


def _make_graph_values(**values: FactoryValueT) -> _GraphValues[FactoryValueT]:
    entries = _normalize_mapping(values)
    construction = _ValuesConstruction(entries=entries, _seal=_VALUES_SEAL)
    return _GraphValues(_construction=construction, _seal=_VALUES_SEAL)


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
        if type(entry.value) is not declaration.descriptor.value_type:
            raise GraphValueAdmissionError(f"{kind} value for {entry.name!r} does not have its exact declared type")
    return entries


def _admit_graph_input_frame(
    frame: GraphInputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphInputFrame[GraphValueT]:
    if type(frame) is not GraphInputFrame:
        raise GraphValueAdmissionError("graph input frame has the wrong nominal type")
    _admit_entries(frame.entries, declarations, kind="graph input")
    return frame


def _admit_node_input_frame(
    frame: NodeInputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeInputFrame[GraphValueT]:
    if type(frame) is not NodeInputFrame:
        raise GraphValueAdmissionError("node input frame has the wrong nominal type")
    _admit_entries(frame.entries, declarations, kind="node input")
    return frame


def _admit_node_output_frame(
    frame: NodeOutputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeOutputFrame[GraphValueT]:
    if type(frame) is not NodeOutputFrame:
        raise GraphValueAdmissionError("node output frame has the wrong nominal type")
    _admit_entries(frame.entries, declarations, kind="node output")
    return frame


def _admit_graph_output_view(
    frame: GraphOutputView[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphOutputView[GraphValueT]:
    if type(frame) is not GraphOutputView:
        raise GraphValueAdmissionError("graph output view has the wrong nominal type")
    _admit_entries(frame.entries, declarations, kind="graph output")
    return frame


def _make_graph_input_frame(
    values: _GraphValues[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphInputFrame[GraphValueT]:
    entries = _admit_entries(_entries_of(values), declarations, kind="graph input")
    return GraphInputFrame(entries=entries, _seal=_FRAME_SEAL)


def _graph_input_from_node_input(
    frame: NodeInputFrame[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphInputFrame[GraphValueT]:
    entries = _admit_entries(frame.entries, declarations, kind="nested graph input")
    return GraphInputFrame(entries=entries, _seal=_FRAME_SEAL)


def _make_node_input_frame(
    entries: tuple[NamedValue[GraphValueT], ...],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeInputFrame[GraphValueT]:
    admitted = _admit_entries(entries, declarations, kind="node input")
    return NodeInputFrame(entries=admitted, _seal=_FRAME_SEAL)


def _make_node_output_frame(
    values: _GraphValues[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeOutputFrame[GraphValueT]:
    entries = _admit_entries(_entries_of(values), declarations, kind="node output")
    return NodeOutputFrame(entries=entries, _seal=_FRAME_SEAL)


def _node_output_from_view(
    view: GraphOutputView[GraphValueT],
    declarations: OutputDeclarations[GraphValueT],
) -> NodeOutputFrame[GraphValueT]:
    entries = _admit_entries(view.entries, declarations, kind="nested node output")
    return NodeOutputFrame(entries=entries, _seal=_FRAME_SEAL)


def _make_graph_output_view(
    entries: tuple[NamedValue[GraphValueT], ...],
    declarations: OutputDeclarations[GraphValueT],
) -> GraphOutputView[GraphValueT]:
    admitted = _admit_entries(entries, declarations, kind="graph output")
    return GraphOutputView(entries=admitted, _seal=_FRAME_SEAL)


def _public_values(view: GraphOutputView[GraphValueT]) -> _GraphValues[GraphValueT]:
    construction = _ValuesConstruction(entries=view.entries, _seal=_VALUES_SEAL)
    return _GraphValues(_construction=construction, _seal=_VALUES_SEAL)


def _public_node_output(frame: NodeOutputFrame[GraphValueT]) -> _GraphValues[GraphValueT]:
    construction = _ValuesConstruction(entries=frame.entries, _seal=_VALUES_SEAL)
    return _GraphValues(_construction=construction, _seal=_VALUES_SEAL)


def _public_node_input(frame: NodeInputFrame[GraphValueT]) -> _GraphValues[GraphValueT]:
    construction = _ValuesConstruction(entries=frame.entries, _seal=_VALUES_SEAL)
    return _GraphValues(_construction=construction, _seal=_VALUES_SEAL)


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


__all__ = [
    "_GraphValues",
    "_admit_graph_input_frame",
    "_admit_graph_output_view",
    "_admit_node_input_frame",
    "_admit_node_output_frame",
    "_frame_value",
    "_graph_input_from_node_input",
    "_make_graph_input_frame",
    "_make_graph_output_view",
    "_make_graph_values",
    "_make_node_input_frame",
    "_make_node_output_frame",
    "_node_output_from_view",
    "_public_node_input",
    "_public_node_output",
    "_public_values",
    "_require_graph_values",
]

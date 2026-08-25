from typing import cast

import pytest

import mote_kernel.execution.graph.values as values_owner
from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValueAdmissionError
from mote_kernel.execution.graph.ports import canonical_nominal_type
from mote_kernel.execution.graph.values import (
    GraphOutputView,
    NamedValue,
    NodeInputFrame,
    NodeOutputFrame,
    _admit_graph_output_view,
    _admit_node_input_frame,
    _admit_node_output_frame,
    _frame_value,
    _make_graph_input_frame,
    _make_node_input_frame,
)


def test_values_factory_copies_and_canonically_orders_keyword_values() -> None:
    values = Graph.values(second=2, first=1)

    assert len(values) == 2
    assert tuple(values) == ("first", "second")
    assert values.keys() == ("first", "second")
    assert values.values() == (1, 2)
    assert values.items() == (("first", 1), ("second", 2))
    assert "first" in values
    assert "missing" not in values
    assert values["second"] == 2
    with pytest.raises(KeyError, match="missing"):
        values["missing"]


def test_values_construction_rejects_a_forged_owner_seal() -> None:
    with pytest.raises(GraphValueAdmissionError, match="canonical owner"):
        eval("_ValuesConstruction(entries=(), _seal=None)", dict(vars(values_owner)))


def test_frame_admission_requires_exact_not_subclass_types() -> None:
    declarations = (("value", canonical_nominal_type(int)),)

    with pytest.raises(GraphValueAdmissionError, match="exact declared type"):
        _make_graph_input_frame(Graph.values(value=True), declarations)


def test_values_and_frame_admission_rejects_each_malformed_internal_shape() -> None:
    declarations = (("value", canonical_nominal_type(int)),)
    values = Graph.values(value=1)
    object.__setattr__(values, "_entries", (NamedValue(" malformed", 1),))
    with pytest.raises(GraphValueAdmissionError, match="malformed canonical names"):
        _make_graph_input_frame(values, declarations)

    malformed_entries = cast(tuple[NamedValue[int], ...], [NamedValue("value", 1)])
    with pytest.raises(GraphValueAdmissionError, match="malformed canonical entries"):
        _make_node_input_frame(malformed_entries, declarations)

    malformed_name = NamedValue(cast(str, 1), 1)
    with pytest.raises(GraphValueAdmissionError, match="malformed canonical names"):
        _make_node_input_frame((malformed_name,), declarations)


def test_each_frame_admission_rejects_a_foreign_nominal_frame() -> None:
    declarations = (("value", canonical_nominal_type(int)),)
    graph_input = _make_graph_input_frame(Graph.values(value=1), declarations)

    with pytest.raises(GraphValueAdmissionError, match="node input frame has the wrong nominal type"):
        _admit_node_input_frame(cast(NodeInputFrame[int], graph_input), declarations)
    with pytest.raises(GraphValueAdmissionError, match="node output frame has the wrong nominal type"):
        _admit_node_output_frame(cast(NodeOutputFrame[int], graph_input), declarations)
    with pytest.raises(GraphValueAdmissionError, match="graph output view has the wrong nominal type"):
        _admit_graph_output_view(cast(GraphOutputView[int], graph_input), declarations)


def test_frame_helpers_preserve_entries_and_find_later_named_values() -> None:
    declarations = (
        ("first", canonical_nominal_type(int)),
        ("second", canonical_nominal_type(int)),
    )
    frame = _make_graph_input_frame(Graph.values(first=1, second=2), declarations)

    assert tuple(entry.name for entry in frame.entries) == ("first", "second")
    assert _frame_value(frame, "second") == 2


def test_frame_value_rejects_a_name_absent_from_the_compiled_descriptor() -> None:
    frame = _make_graph_input_frame(
        Graph.values(value=1),
        (("value", canonical_nominal_type(int)),),
    )

    with pytest.raises(GraphValueAdmissionError, match="does not contain"):
        _frame_value(frame, "missing")

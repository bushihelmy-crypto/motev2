import typing
from typing import cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.ports import (
    NodeOutputRef,
    PredecessorOutputRef,
    PublicationSelection,
    PublicationSelectionKind,
    canonical_nominal_type,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.state.graph_state import GraphNodeId


def test_nominal_type_rejects_a_nonclass_declaration() -> None:
    with pytest.raises(GraphValidationError, match="concrete nominal class"):
        canonical_nominal_type("str")


@pytest.mark.parametrize("value_type", [object, typing.Any])
def test_nominal_type_rejects_unbounded_top_types(value_type: type[object]) -> None:
    with pytest.raises(GraphValidationError, match="concrete nominal class"):
        canonical_nominal_type(value_type)


def test_nominal_type_identity_does_not_depend_on_mutable_class_metadata() -> None:
    class Concrete:
        pass

    Concrete.__module__ = "typing"
    Concrete.__qualname__ = "Any"

    assert canonical_nominal_type(Concrete).value_type is Concrete


def test_publication_selection_resolves_absolute_and_relative_coordinates() -> None:
    absolute = PublicationSelection(PublicationSelectionKind.ABSOLUTE, 2)
    relative = PublicationSelection(PublicationSelectionKind.RELATIVE, 2)

    assert absolute.resolve(8) == 2
    assert relative.resolve(8) == 6


def test_relative_publication_selection_cannot_precede_the_run() -> None:
    selection = PublicationSelection(PublicationSelectionKind.RELATIVE, 2)

    with pytest.raises(GraphValidationError, match="precedes"):
        selection.resolve(1)


def test_input_normalizer_requires_a_mapping() -> None:
    with pytest.raises(GraphValidationError, match="inputs must be a mapping"):
        normalize_input_bindings(None)


def test_input_normalizer_rejects_type_declarations_in_source_position() -> None:
    with pytest.raises(GraphValidationError, match="must bind"):
        normalize_input_bindings({"value": str})


def test_output_normalizer_requires_a_mapping() -> None:
    with pytest.raises(GraphValidationError, match="outputs must be a mapping"):
        normalize_output_declarations(None)


def test_output_normalizer_rejects_value_references_in_type_position() -> None:
    with pytest.raises(GraphValidationError, match="must declare"):
        normalize_output_declarations({"value": Graph.node_output("source", "value")})


def test_graph_output_normalizer_requires_a_mapping() -> None:
    with pytest.raises(GraphValidationError, match="graph outputs must be a mapping"):
        normalize_graph_output_declarations(None)


def test_graph_output_normalizer_rejects_type_declarations_in_source_position() -> None:
    with pytest.raises(GraphValidationError, match="must bind"):
        normalize_graph_output_declarations({"value": str})


def test_graph_node_output_overloads_keep_fixed_and_causal_addresses_distinct() -> None:
    assert Graph.node_output("producer", "value") == NodeOutputRef(GraphNodeId("producer"), "value")
    assert Graph.node_output("value") == PredecessorOutputRef("value")


def test_predecessor_output_name_is_canonicalized_at_the_facade() -> None:
    with pytest.raises(GraphValidationError, match="source output"):
        Graph.node_output(" value ")


def test_predecessor_output_reference_is_only_valid_as_a_node_input() -> None:
    causal = cast(NodeOutputRef, Graph.node_output("value"))

    with pytest.raises(GraphValidationError, match="graph output 'value' must bind"):
        normalize_graph_output_declarations({"value": causal})

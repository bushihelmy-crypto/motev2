import typing
from collections.abc import Mapping
from typing import cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.ports import (
    FeedbackInputBinding,
    GraphInputRef,
    NodeOutputRef,
    PublicationSelection,
    PublicationSelectionKind,
    canonical_nominal_type,
    normalize_facade_input_bindings,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)


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


def test_feedback_binding_requires_exact_internal_reference_types() -> None:
    seed = Graph.graph_input("seed", str)
    repeat = Graph.node_output("loop", "value")

    with pytest.raises(GraphValidationError, match="feedback initial"):
        FeedbackInputBinding(cast(GraphInputRef[str], object()), repeat)
    with pytest.raises(GraphValidationError, match="feedback repeat"):
        FeedbackInputBinding(seed, cast(NodeOutputRef, object()))


def test_public_facade_normalizer_rejects_internal_feedback_declarations() -> None:
    feedback = FeedbackInputBinding(
        Graph.graph_input("seed", str),
        Graph.node_output("loop", "value"),
    )
    values = cast(
        Mapping[str, GraphInputRef[str] | NodeOutputRef],
        {"value": feedback},
    )

    with pytest.raises(GraphValidationError, match=r"not available through Graph\.add_node"):
        normalize_facade_input_bindings(values)

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    NodeOutputRef,
    canonical_nominal_type,
)
from mote_kernel.state.graph_state import GraphNodeId


async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(value=values["value"])


def child_with_node_output(definition_id: str = "nested.child") -> Graph[str]:
    child = Graph[str](definition_id)
    child.add_node(
        "leaf",
        echo,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    child.set_outputs({"value": Graph.node_output("leaf", "value")})
    return child


def test_output_ref_reuses_an_ordinary_node_declaration_descriptor() -> None:
    graph = child_with_node_output("output-ref.ordinary")

    output = graph.output_ref("leaf", "value")

    assert type(output) is NodeOutputRef
    assert output.descriptor is not None
    assert output.descriptor.value_type is str


@pytest.mark.asyncio
async def test_output_ref_resolves_a_nested_child_boundary_and_runs() -> None:
    child = child_with_node_output("output-ref.child")
    parent = Graph[str]("output-ref.parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})

    output = parent.output_ref("child", "value")
    with pytest.raises(Graph.ValidationError, match="immutable"):
        child.set_outputs({"value": Graph.node_output("leaf", "value")})
    parent.set_outputs({"value": output})

    result = await parent.run(Graph.values(value="nested"))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "nested"


@pytest.mark.asyncio
async def test_output_ref_resolves_a_nested_boundary_sourced_from_graph_input() -> None:
    child = Graph[str]("output-ref.input-child")
    graph_input = Graph.graph_input("value", str)
    child.add_node("leaf", echo, inputs={"value": graph_input}, outputs={"value": str})
    child.set_outputs({"value": graph_input})
    parent = Graph[str]("output-ref.input-parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})

    output = parent.output_ref("child", "value")
    parent.set_outputs({"value": output})
    result = await parent.run(Graph.values(value="input"))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "input"


def test_output_ref_resolves_through_multiple_nested_boundaries() -> None:
    leaf = child_with_node_output("output-ref.deep-leaf")
    middle = Graph[str]("output-ref.deep-middle")
    middle.add_node("inner", leaf, inputs={"value": Graph.graph_input("value", str)})
    middle_output = middle.output_ref("inner", "value")
    middle.set_outputs({"value": middle_output})
    root = Graph[str]("output-ref.deep-root")
    root.add_node("middle", middle, inputs={"value": Graph.graph_input("value", str)})

    output = root.output_ref("middle", "value")

    assert output.descriptor is middle_output.descriptor


def test_output_ref_resolves_an_address_only_boundary_reference() -> None:
    child = child_with_node_output("output-ref.address-only")
    parent = Graph[str]("output-ref.address-only-parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})

    output = parent.output_ref("child", "value")

    assert output.descriptor is not None
    assert output.descriptor.value_type is str


def test_output_ref_rejects_unknown_nodes_and_outputs() -> None:
    graph = child_with_node_output("output-ref.unknown")

    with pytest.raises(Graph.ValidationError, match="exactly one declared node"):
        graph.output_ref("missing", "value")
    with pytest.raises(Graph.ValidationError, match="does not declare output"):
        graph.output_ref("leaf", "missing")


def test_output_ref_rejects_a_nested_graph_without_a_boundary() -> None:
    child = Graph[str]("output-ref.no-boundary-child")
    child.add_node("leaf", echo, inputs={"value": Graph.graph_input("value", str)}, outputs={})
    parent = Graph[str]("output-ref.no-boundary-parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})

    with pytest.raises(Graph.ValidationError, match="set_outputs"):
        parent.output_ref("child", "value")


def test_output_ref_rejects_an_unknown_nested_boundary_name() -> None:
    child = child_with_node_output("output-ref.unknown-boundary-child")
    parent = Graph[str]("output-ref.unknown-boundary-parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})

    with pytest.raises(Graph.ValidationError, match="boundary output"):
        parent.output_ref("child", "missing")


def test_output_ref_rejects_a_foreign_nested_descriptor() -> None:
    child = Graph[str]("output-ref.foreign-child")
    child.add_node("leaf", echo, inputs={"value": Graph.graph_input("value", str)}, outputs={"value": str})
    declared = Graph.node_output("leaf", "value")
    child.set_outputs({"value": replace(declared, descriptor=canonical_nominal_type(int))})
    parent = Graph[str]("output-ref.foreign-parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})

    with pytest.raises(Graph.ValidationError, match="does not match its declared exact type/descriptor"):
        parent.output_ref("child", "value")


def test_output_ref_rejects_recursive_nested_composition() -> None:
    graph = Graph[str]("output-ref.recursive")
    graph.add_node("self", graph, inputs={})
    graph.set_outputs({"value": Graph.graph_input("value", str)})

    with pytest.raises(Graph.ValidationError, match="recursively"):
        graph.output_ref("self", "value")


def test_output_ref_rejects_conflicting_graph_input_declarations() -> None:
    child = Graph[str | int]("output-ref.conflicting-input-child")

    async def consume(_values: Graph.Values[str | int]) -> Graph.Values[str | int]:
        return Graph.values()

    child.add_node("left", consume, inputs={"value": Graph.graph_input("value", str)}, outputs={})
    child.add_node("right", consume, inputs={"value": Graph.graph_input("value", int)}, outputs={})
    child.set_outputs({"value": Graph.graph_input("value", str)})
    graph = Graph[str | int]("output-ref.conflicting-input-parent")
    graph.add_node("child", child, inputs={})

    with pytest.raises(Graph.ValidationError, match="conflicting exact type"):
        graph.output_ref("child", "value")


def test_output_ref_rejects_duplicate_node_declarations() -> None:
    graph = Graph[str]("output-ref.duplicate-node")
    graph.add_node("leaf", echo, inputs={}, outputs={})
    graph.add_node("leaf", echo, inputs={}, outputs={})

    with pytest.raises(Graph.ValidationError, match="exactly one declared node"):
        graph.output_ref("leaf", "value")


def test_output_ref_can_be_used_as_a_typed_binding_source() -> None:
    child = child_with_node_output("output-ref.binding-child")
    parent = Graph[str]("output-ref.binding-parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})
    child_output = parent.output_ref("child", "value")
    binding = Graph.bind("value", child_output)

    assert binding.source is child_output
    assert binding.name == "value"
    assert binding.source.descriptor is child_output.descriptor


def test_output_ref_preserves_the_compiler_selected_graph_input_descriptor() -> None:
    child = Graph[str]("output-ref.input-identity-child")
    first = Graph.graph_input("value", str)
    second = Graph.graph_input("value", str)
    child.add_node("leaf", echo, inputs={"value": first}, outputs={})
    child.set_outputs({"value": second})
    parent = Graph[str]("output-ref.input-identity-parent")
    parent.add_node("child", child, inputs={"value": Graph.graph_input("value", str)})

    output = parent.output_ref("child", "value")

    assert output.descriptor is second.descriptor
    assert output.descriptor is not first.descriptor


def test_output_ref_is_available_after_compile_and_keeps_identity() -> None:
    graph = child_with_node_output("output-ref.after-compile")
    first = graph.output_ref("leaf", "value")

    asyncio.run(graph.run(Graph.values(value="compiled")))
    second = graph.output_ref("leaf", "value")

    assert second.descriptor is first.descriptor


def test_node_output_typed_predecessor_requires_a_typed_reference_and_no_extra_name() -> None:
    untyped = Graph.node_output("producer", "value")
    with pytest.raises(Graph.ValidationError, match="typed predecessor output"):
        Graph.node_output(untyped)

    typed = NodeOutputRef(GraphNodeId("producer"), "value", canonical_nominal_type(str))
    with pytest.raises(Graph.ValidationError, match="typed predecessor output"):
        Graph.node_output(cast(str, typed), "other")

    predecessor = Graph.node_output(typed)
    assert predecessor.output_name == "value"
    assert predecessor.descriptor is typed.descriptor


def test_typed_bind_rejects_a_non_port_object_before_reading_its_descriptor() -> None:
    with pytest.raises(Graph.ValidationError, match="typed input must bind"):
        Graph.bind("value", cast(GraphInputRef[str], object()))


def test_output_ref_graph_input_resolution_skips_unrelated_node_and_boundary_inputs() -> None:
    child = Graph[str]("output-ref.input-skips")
    target = Graph.graph_input("target", str)
    unrelated = Graph.graph_input("unrelated", str)
    child.add_node("leaf", echo, inputs={"unrelated": unrelated}, outputs={"value": str})
    child.set_outputs({"unrelated": unrelated, "value": target})
    parent = Graph[str]("output-ref.input-skips-parent")
    parent.add_node("child", child, inputs={})

    output = parent.output_ref("child", "value")

    assert output.descriptor is target.descriptor


def test_output_ref_rejects_conflicting_graph_input_boundary_declarations() -> None:
    child = Graph[str | int]("output-ref.conflicting-boundaries")
    child.set_outputs(
        {
            "first": Graph.graph_input("shared", str),
            "second": Graph.graph_input("shared", int),
        }
    )
    parent = Graph[str | int]("output-ref.conflicting-boundaries-parent")
    parent.add_node("child", child, inputs={})

    with pytest.raises(Graph.ValidationError, match="conflicting exact type"):
        parent.output_ref("child", "first")


def test_add_node_rejects_partial_typed_declarations_before_assembly() -> None:
    graph = Graph[str]("output-ref.partial-typed")

    async def operation(value: str) -> str:
        return value

    binding = Graph.bind("value", Graph.graph_input("value", str))
    add_node = cast(Callable[..., object], graph.add_node)
    with pytest.raises(Graph.ValidationError, match="require input, materializer"):
        add_node("node", operation, inputs=(binding,), input_type=str)


def test_add_node_rejects_typed_nodes_with_ordinary_output_or_input_shapes() -> None:
    graph = Graph[str]("output-ref.typed-shape")

    async def operation(value: str) -> str:
        return value

    binding = Graph.bind("value", Graph.graph_input("value", str))

    def materialize(values: Graph.Inputs[str]) -> str:
        return values.get(binding)

    add_node = cast(Callable[..., object], graph.add_node)

    with pytest.raises(Graph.ValidationError, match="typed bindings and one typed output"):
        add_node(
            "with-outputs",
            operation,
            inputs=(binding,),
            outputs={},
            input_type=str,
            materialize=materialize,
            output_name="result",
            output_type=str,
        )
    with pytest.raises(Graph.ValidationError, match="typed bindings and one typed output"):
        add_node(
            "with-mapping",
            operation,
            inputs=cast(tuple[Graph.InputBinding[str], ...], {}),
            input_type=str,
            materialize=materialize,
            output_name="result",
            output_type=str,
        )

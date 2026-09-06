import asyncio
from dataclasses import replace

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import NodeOutputRef, canonical_nominal_type


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


def test_output_ref_repairs_a_descriptorless_legacy_boundary_reference() -> None:
    child = child_with_node_output("output-ref.legacy")
    parent = Graph[str]("output-ref.legacy-parent")
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

    with pytest.raises(Graph.ValidationError, match="foreign descriptor"):
        parent.output_ref("child", "value")


def test_output_ref_rejects_recursive_nested_composition() -> None:
    graph = Graph[str]("output-ref.recursive")
    graph.add_node("self", graph, inputs={})
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
    assert binding.destination.descriptor is child_output.descriptor


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


def test_output_ref_requires_a_concrete_boundary_descriptor() -> None:
    child = Graph[str]("output-ref.bad-descriptor-child")
    child.add_node("leaf", echo, inputs={}, outputs={"value": str})
    source = Graph.node_output("leaf", "value")
    object.__setattr__(source, "descriptor", object())
    child.set_outputs({"value": source})
    parent = Graph[str]("output-ref.bad-descriptor-parent")
    parent.add_node("child", child, inputs={})

    with pytest.raises(Graph.ValidationError, match="malformed nominal descriptor"):
        parent.output_ref("child", "value")


def test_output_ref_is_available_after_compile_and_keeps_identity() -> None:
    graph = child_with_node_output("output-ref.after-compile")
    first = graph.output_ref("leaf", "value")

    asyncio.run(graph.run(Graph.values(value="compiled")))
    second = graph.output_ref("leaf", "value")

    assert second.descriptor is first.descriptor

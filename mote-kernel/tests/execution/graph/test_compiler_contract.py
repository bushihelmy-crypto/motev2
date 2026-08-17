from collections.abc import Mapping
from typing import TypeAlias

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    DuplicateEdgeError,
    GraphValidationError,
    UnknownNodeError,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    GraphOutputDeclarations,
    NodeOutputPort,
    NodeOutputRef,
    PublicationSelectionKind,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
)

PipelineValue: TypeAlias = str | int


async def identity(values: Graph.Values[PipelineValue], /) -> Graph.Values[PipelineValue]:
    return values


def node(
    node_id: str,
    *,
    inputs: Mapping[str, GraphInputRef[PipelineValue] | NodeOutputRef],
    outputs: Mapping[str, type[PipelineValue]],
) -> CallableNodeDefinition[PipelineValue]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        identity,
        normalize_input_bindings(inputs),
        normalize_output_declarations(outputs),
    )


def definition(
    nodes: tuple[
        CallableNodeDefinition[PipelineValue] | NestedGraphNodeDefinition[PipelineValue],
        ...,
    ],
    *,
    edges: tuple[Edge, ...] = (),
    entries: tuple[str, ...] = (),
    outputs: GraphOutputDeclarations[PipelineValue] | None = None,
    definition_id: str = "compiler.contract",
) -> GraphDefinition[PipelineValue]:
    return GraphDefinition(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(1),
        nodes,
        edges,
        tuple(GraphNodeId(entry) for entry in entries),
        normalize_graph_output_declarations({}) if outputs is None else outputs,
    )


def test_compiler_resolves_a_later_named_output_declaration() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"first": str, "second": str},
    )
    consumer = node(
        "consumer",
        inputs={"selected": Graph.node_output("source", "second")},
        outputs={},
    )

    compiled = compile_graph(definition((source, consumer)))

    binding = compiled.materializations[GraphNodeId("consumer")].bindings.entries[0]
    assert isinstance(binding.source, NodeOutputPort)
    assert binding.source.output_name == "second"


def test_compiler_rejects_an_unknown_output_port() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    consumer = node(
        "consumer",
        inputs={"value": Graph.node_output("source", "missing")},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="unknown output port"):
        compile_graph(definition((source, consumer)))


def test_compiler_rejects_conflicting_graph_input_exact_types() -> None:
    string_node = node(
        "string",
        inputs={"value": Graph.graph_input("shared", str)},
        outputs={},
    )
    integer_node = node(
        "integer",
        inputs={"value": Graph.graph_input("shared", int)},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="conflicting exact type"):
        compile_graph(definition((string_node, integer_node)))


def test_compiler_rejects_a_node_binding_its_own_output() -> None:
    recursive = node(
        "recursive",
        inputs={"value": Graph.node_output("recursive", "value")},
        outputs={"value": str},
    )

    with pytest.raises(GraphValidationError, match="cannot bind its own output"):
        compile_graph(definition((recursive,)))


def test_compiler_rejects_a_value_source_from_an_unknown_node() -> None:
    consumer = node(
        "consumer",
        inputs={"value": Graph.node_output("unknown", "value")},
        outputs={},
    )

    with pytest.raises(UnknownNodeError, match="value source"):
        compile_graph(definition((consumer,)))


def test_compiler_rejects_an_ordinary_data_cycle() -> None:
    left = node(
        "left",
        inputs={"value": Graph.node_output("right", "value")},
        outputs={"value": str},
    )
    right = node(
        "right",
        inputs={"value": Graph.node_output("left", "value")},
        outputs={"value": str},
    )

    with pytest.raises(GraphValidationError, match="data cycle"):
        compile_graph(definition((left, right)))


def test_compiler_rejects_duplicate_data_and_direct_control_pair() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )

    with pytest.raises(DuplicateEdgeError, match="duplicate"):
        compile_graph(
            definition(
                (source, target),
                edges=(DirectEdge(GraphNodeId("source"), GraphNodeId("target")),),
            )
        )


def test_compiler_rejects_explicit_start_that_duplicates_automatic_entry() -> None:
    entry = node("entry", inputs={}, outputs={})

    with pytest.raises(DuplicateBoundaryError, match="automatic entry"):
        compile_graph(definition((entry,), entries=("entry",)))


def test_compiler_rejects_explicit_start_target_requiring_node_output() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="explicit START target"):
        compile_graph(definition((source, target), entries=("target",)))


def test_compiler_rejects_control_path_that_can_reach_its_required_producer() -> None:
    controller = node("controller", inputs={}, outputs={})
    source = node("source", inputs={}, outputs={"value": str})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    edges = (
        DirectEdge(GraphNodeId("controller"), GraphNodeId("target")),
        DirectEdge(GraphNodeId("target"), GraphNodeId("source")),
    )

    with pytest.raises(GraphValidationError, match="not guaranteed before controlled"):
        compile_graph(definition((controller, source, target), edges=edges))


def test_compiler_rejects_control_gate_without_required_producer_guarantee() -> None:
    controller = node("controller", inputs={}, outputs={})
    source = node("source", inputs={}, outputs={"value": str})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="can activate before required producers"):
        compile_graph(
            definition(
                (controller, source, target),
                edges=(DirectEdge(GraphNodeId("controller"), GraphNodeId("target")),),
            )
        )


def test_compiler_requires_nested_inputs_to_match_child_boundary_exactly() -> None:
    child_step = node(
        "child-step",
        inputs={"child_input": Graph.graph_input("child_input", str)},
        outputs={},
    )
    child = definition((child_step,), definition_id="compiler.child")
    nested = NestedGraphNodeDefinition(
        GraphNodeId("nested"),
        child,
        normalize_input_bindings({"wrong": Graph.graph_input("wrong", str)}),
    )

    with pytest.raises(GraphValidationError, match="exactly match child boundary"):
        compile_graph(definition((nested,)))


def test_compiler_uses_relative_selection_for_loop_producer_data_trigger() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    compiled = compile_graph(
        definition(
            (source, target),
            edges=(DirectEdge(GraphNodeId("source"), GraphNodeId("source")),),
            entries=("source",),
        )
    )

    selection = compiled.materializations[GraphNodeId("target")].bindings.entries[0].publication
    assert selection is not None
    assert selection.kind is PublicationSelectionKind.RELATIVE
    assert selection.superstep == 1


def test_compiler_rejects_ambiguous_loop_publication_for_join_consumer() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    gate = node("gate", inputs={}, outputs={})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    edges = (
        DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
        JoinEdge((GraphNodeId("source"), GraphNodeId("gate")), GraphNodeId("target")),
    )

    with pytest.raises(GraphValidationError, match="no unique activation coordinate"):
        compile_graph(definition((source, gate, target), edges=edges, entries=("source",)))


def test_compiler_uses_relative_selection_for_loop_graph_output() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    compiled = compile_graph(
        definition(
            (source,),
            edges=(
                DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
                DirectEdge(GraphNodeId("source"), END),
            ),
            entries=("source",),
            outputs=normalize_graph_output_declarations({"value": Graph.node_output("source", "value")}),
        )
    )

    selection = compiled.graph_outputs.entries[0].publication
    assert selection is not None
    assert selection.kind is PublicationSelectionKind.RELATIVE
    assert selection.superstep == 0


def test_compiler_rejects_ambiguous_loop_graph_output_completion() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    gate = node("gate", inputs={}, outputs={})
    edges = (
        DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
        DirectEdge(GraphNodeId("gate"), GraphNodeId("gate")),
        JoinEdge((GraphNodeId("source"), GraphNodeId("gate")), END),
    )

    with pytest.raises(GraphValidationError, match="no unique completion activation coordinate"):
        compile_graph(
            definition(
                (source, gate),
                edges=edges,
                entries=("source", "gate"),
                outputs=normalize_graph_output_declarations({"value": Graph.node_output("source", "value")}),
            )
        )


def test_compiler_rejects_output_not_guaranteed_on_every_terminal_branch() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={"value": str})
    right = node("right", inputs={}, outputs={})
    edges: tuple[Edge, ...] = (
        ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
        ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
    )

    with pytest.raises(GraphValidationError, match="not guaranteed before every successful completion"):
        compile_graph(
            definition(
                (decision, left, right),
                edges=edges,
                outputs=normalize_graph_output_declarations({"value": Graph.node_output("left", "value")}),
            )
        )


def test_join_to_end_is_one_terminal_gate_for_output_guarantees() -> None:
    left = node("left", inputs={}, outputs={"value": str})
    right = node("right", inputs={}, outputs={})

    compiled = compile_graph(
        definition(
            (left, right),
            edges=(JoinEdge((GraphNodeId("left"), GraphNodeId("right")), END),),
            outputs=normalize_graph_output_declarations({"value": Graph.node_output("left", "value")}),
        )
    )

    assert compiled.graph_outputs.entries[0].source == NodeOutputPort((), GraphNodeId("left"), "value")


def test_compiler_rejects_a_join_between_mutually_exclusive_routes() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})
    joined = node("joined", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="jointly satisfiable"):
        compile_graph(
            definition(
                (decision, left, right, joined),
                edges=(
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                    JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
                ),
            )
        )


def test_compiler_rejects_a_terminal_join_between_mutually_exclusive_routes() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="mutually exclusive activation sources"):
        compile_graph(
            definition(
                (decision, left, right),
                edges=(
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                    JoinEdge((GraphNodeId("left"), GraphNodeId("right")), END),
                ),
            )
        )


def test_compiler_rejects_a_join_that_can_receive_only_one_source_on_a_route() -> None:
    decision = node("decision", inputs={}, outputs={})
    selected = node("selected", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="partial source set"):
        compile_graph(
            definition(
                (decision, selected),
                edges=(
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("finish"), END),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("selected"), GraphNodeId("selected")),
                    JoinEdge((GraphNodeId("decision"), GraphNodeId("selected")), END),
                ),
            )
        )


def test_compiler_accepts_a_join_when_a_direct_path_coexists_with_the_selected_route() -> None:
    decision = node("decision", inputs={}, outputs={})
    always = node("always", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})

    compiled = compile_graph(
        definition(
            (decision, always, left, right),
            edges=(
                DirectEdge(GraphNodeId("decision"), GraphNodeId("always")),
                DirectEdge(GraphNodeId("decision"), GraphNodeId("right")),
                ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                JoinEdge((GraphNodeId("always"), GraphNodeId("right")), END),
            ),
        )
    )

    assert compiled.transition.conditional_targets[GraphNodeId("decision")][GraphRouteId("right")] == GraphNodeId(
        "right"
    )


def test_compiler_preserves_cross_superstep_join_possibility_in_a_control_loop() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})

    compiled = compile_graph(
        definition(
            (decision, left, right),
            edges=(
                ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                DirectEdge(GraphNodeId("left"), GraphNodeId("decision")),
                DirectEdge(GraphNodeId("right"), GraphNodeId("decision")),
                JoinEdge((GraphNodeId("left"), GraphNodeId("right")), END),
            ),
            entries=("decision",),
        )
    )

    assert compiled.transition.joins_by_source[GraphNodeId("left")]

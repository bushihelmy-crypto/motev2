from collections.abc import Mapping
from typing import TypeAlias, cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    FeedbackInputBinding,
    GraphInputRef,
    GraphOutputDeclarations,
    NodeOutputRef,
    PublicationSelectionKind,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId, GraphRouteId

Value: TypeAlias = int


async def identity(values: Graph.Values[Value], /) -> Graph.Values[Value]:
    return values


def _node(
    node_id: str,
    inputs: Mapping[str, GraphInputRef[Value] | NodeOutputRef | FeedbackInputBinding[Value]],
    outputs: Mapping[str, type[Value]],
) -> CallableNodeDefinition[Value]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        identity,
        normalize_input_bindings(inputs),
        normalize_output_declarations(outputs),
    )


def _feedback_definition(
    *,
    inputs: Mapping[str, GraphInputRef[Value] | NodeOutputRef | FeedbackInputBinding[Value]] | None = None,
    edges: tuple[Edge, ...] | None = None,
    outputs: GraphOutputDeclarations[Value] | None = None,
) -> GraphDefinition[Value]:
    seed = Graph.graph_input("seed", int)
    loop_inputs = {"value": FeedbackInputBinding(seed, Graph.node_output("loop", "value"))}
    if inputs is not None:
        loop_inputs = dict(inputs)
    loop_edges = (
        ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
        ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
    )
    if edges is not None:
        loop_edges = edges
    loop_outputs: GraphOutputDeclarations[Value] = normalize_graph_output_declarations(
        {"result": Graph.node_output("loop", "value")}
    )
    return GraphDefinition(
        GraphDefinitionId("feedback.compiler"),
        GraphDefinitionVersion(1),
        (_node("loop", loop_inputs, {"value": int}),),
        loop_edges,
        (GraphNodeId("loop"),),
        loop_outputs if outputs is None else outputs,
    )


def test_compiler_builds_one_immutable_self_feedback_rule() -> None:
    compiled = compile_graph(_feedback_definition())

    rules = compiled.transition.activation_rules.entries
    assert len(rules) == 1
    rule = rules[0]
    assert rule.target == GraphNodeId("loop")
    assert rule.input_name == "value"
    assert rule.repeat.node_id == GraphNodeId("loop")
    assert rule.repeat.output_name == "value"
    assert rule.feedback_route == GraphRouteId("continue")
    assert rule.terminal_route == GraphRouteId("done")
    assert rule.repeat_selection.kind is PublicationSelectionKind.RELATIVE
    assert rule.repeat_selection.superstep == 1

    binding = compiled.transition.materializations[GraphNodeId("loop")].bindings.entries[0]
    assert binding.source is rule


def test_compiler_rejects_ordinary_self_data_cycle_even_with_feedback_types_available() -> None:
    loop = _node(
        "loop",
        {"value": Graph.node_output("loop", "value")},
        {"value": int},
    )

    with pytest.raises(GraphValidationError, match="cannot bind its own output"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("ordinary.self"),
                GraphDefinitionVersion(1),
                (loop,),
                (),
                (),
                normalize_graph_output_declarations({}),
            )
        )


def test_compiler_rejects_feedback_seed_from_a_node_output() -> None:
    with pytest.raises(GraphValidationError, match="initial source"):
        compile_graph(
            _feedback_definition(
                inputs={
                    "value": FeedbackInputBinding(
                        Graph.node_output("loop", "value"),
                        Graph.node_output("loop", "value"),
                    )
                }
            )
        )


def test_compiler_rejects_feedback_without_a_terminal_route() -> None:
    with pytest.raises(GraphValidationError):
        compile_graph(
            _feedback_definition(
                edges=(ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),)
            )
        )


def test_compiler_rejects_feedback_with_an_extra_control_edge() -> None:
    with pytest.raises(GraphValidationError, match="ordinary or join"):
        compile_graph(
            _feedback_definition(
                edges=(
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
                    DirectEdge(GraphNodeId("loop"), END),
                )
            )
        )


def test_compiler_requires_feedback_output_to_be_the_terminal_repeat_publication() -> None:
    with pytest.raises(GraphValidationError, match="repeat output"):
        compile_graph(
            _feedback_definition(
                outputs=normalize_graph_output_declarations({"result": Graph.graph_input("seed", int)})
            )
        )


def test_compiler_rejects_feedback_inside_a_nested_graph_in_the_first_slice() -> None:
    child = _feedback_definition()
    nested = NestedGraphNodeDefinition(
        GraphNodeId("nested"),
        child,
        normalize_input_bindings({"seed": Graph.graph_input("seed", int)}),
    )
    parent = GraphDefinition(
        GraphDefinitionId("feedback.parent"),
        GraphDefinitionVersion(1),
        (nested,),
        (),
        (),
        normalize_graph_output_declarations({}),
    )

    with pytest.raises(GraphValidationError, match="nested graph"):
        compile_graph(parent)


def test_compiler_rejects_multiple_feedback_bindings() -> None:
    seed = Graph.graph_input("seed", int)

    with pytest.raises(GraphValidationError, match="exactly one feedback input"):
        compile_graph(
            _feedback_definition(
                inputs={
                    "first": FeedbackInputBinding(seed, Graph.node_output("loop", "value")),
                    "second": FeedbackInputBinding(seed, Graph.node_output("loop", "value")),
                }
            )
        )


def test_compiler_rejects_feedback_repeat_from_another_node() -> None:
    seed = Graph.graph_input("seed", int)
    loop = _node(
        "loop",
        {"value": FeedbackInputBinding(seed, Graph.node_output("producer", "value"))},
        {"value": int},
    )
    producer = _node("producer", {"value": seed}, {"value": int})
    definition = GraphDefinition(
        GraphDefinitionId("feedback.other-producer"),
        GraphDefinitionVersion(1),
        (loop, producer),
        (
            ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
            ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
            DirectEdge(GraphNodeId("producer"), GraphNodeId("loop")),
        ),
        (GraphNodeId("loop"), GraphNodeId("producer")),
        normalize_graph_output_declarations({"result": Graph.node_output("loop", "value")}),
    )

    with pytest.raises(GraphValidationError):
        compile_graph(definition)


def test_compiler_rejects_feedback_repeat_from_an_unknown_node() -> None:
    seed = Graph.graph_input("seed", int)

    with pytest.raises(GraphValidationError, match="feedback repeat references unknown node"):
        compile_graph(
            _feedback_definition(
                inputs={
                    "value": FeedbackInputBinding(
                        seed,
                        Graph.node_output("missing", "value"),
                    )
                }
            )
        )


def test_compiler_rejects_feedback_initial_and_repeat_type_mismatch() -> None:
    seed = Graph.graph_input("seed", str)
    loop = _node(
        "loop",
        {
            "value": cast(
                FeedbackInputBinding[Value],
                FeedbackInputBinding(seed, Graph.node_output("loop", "value")),
            )
        },
        {"value": int},
    )
    definition = GraphDefinition(
        GraphDefinitionId("feedback.type-mismatch"),
        GraphDefinitionVersion(1),
        (loop,),
        (
            ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
            ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
        ),
        (GraphNodeId("loop"),),
        normalize_graph_output_declarations({"result": Graph.node_output("loop", "value")}),
    )

    with pytest.raises(GraphValidationError, match="different exact types"):
        compile_graph(definition)


def test_compiler_rejects_multiple_terminal_routes() -> None:
    with pytest.raises(GraphValidationError, match="exactly one self feedback route and one terminal route"):
        compile_graph(
            _feedback_definition(
                edges=(
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("cancel"), END),
                )
            )
        )


def test_compiler_requires_exactly_one_feedback_graph_output() -> None:
    with pytest.raises(GraphValidationError, match="exactly one graph output"):
        compile_graph(
            _feedback_definition(
                outputs=normalize_graph_output_declarations({}),
            )
        )


def test_compiler_rejects_a_feedback_graph_with_an_extra_node() -> None:
    seed = Graph.graph_input("seed", int)
    loop = _node(
        "loop",
        {"value": FeedbackInputBinding(seed, Graph.node_output("loop", "value"))},
        {"value": int},
    )
    extra = _node("extra", {"value": seed}, {"value": int})

    with pytest.raises(GraphValidationError):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.extra-node"),
                GraphDefinitionVersion(1),
                (loop, extra),
                (
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
                    DirectEdge(GraphNodeId("loop"), GraphNodeId("extra")),
                ),
                (GraphNodeId("loop"),),
                normalize_graph_output_declarations({"result": Graph.node_output("loop", "value")}),
            )
        )


def test_compiler_rejects_feedback_join_control() -> None:
    seed = Graph.graph_input("seed", int)
    loop = _node(
        "loop",
        {"value": FeedbackInputBinding(seed, Graph.node_output("loop", "value"))},
        {"value": int},
    )
    other = _node("other", {"value": seed}, {"value": int})

    with pytest.raises(GraphValidationError):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.join"),
                GraphDefinitionVersion(1),
                (loop, other),
                (
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
                    # A feedback target cannot also be admitted through a Join.
                    # The extra node is deliberately present so the edge is a
                    # valid definition before the feedback validator runs.
                    JoinEdge((GraphNodeId("loop"), GraphNodeId("other")), GraphNodeId("loop")),
                ),
                (GraphNodeId("loop"), GraphNodeId("other")),
                normalize_graph_output_declarations({"result": Graph.node_output("loop", "value")}),
            )
        )


def test_compiler_rejects_an_additional_control_source_for_the_feedback_target() -> None:
    seed = Graph.graph_input("seed", int)
    loop = _node(
        "loop",
        {"value": FeedbackInputBinding(seed, Graph.node_output("loop", "value"))},
        {"value": int},
    )
    source = _node("source", {"value": seed}, {"value": int})

    with pytest.raises(GraphValidationError):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.multiple-control-sources"),
                GraphDefinitionVersion(1),
                (loop, source),
                (
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
                    DirectEdge(GraphNodeId("source"), GraphNodeId("loop")),
                ),
                (GraphNodeId("loop"),),
                normalize_graph_output_declarations({"result": Graph.node_output("loop", "value")}),
            )
        )

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
    GraphInputPort,
    GraphInputRef,
    GraphOutputDeclarations,
    NodeOutputPort,
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
    node_outputs: Mapping[str, type[Value]] | None = None,
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
        (_node("loop", loop_inputs, {"value": int} if node_outputs is None else node_outputs),),
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
    assert rule.repeat_gates == (((GraphNodeId("loop"), frozenset({GraphRouteId("continue")})),),)
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


def test_compiler_rejects_a_target_output_as_initial_source_even_with_an_external_repeat() -> None:
    loop = _node(
        "loop",
        {
            "value": FeedbackInputBinding(
                Graph.node_output("loop", "value"),
                Graph.node_output("producer", "value"),
            )
        },
        {"value": int},
    )
    producer = _node("producer", {"value": Graph.node_output("loop", "value")}, {"value": int})
    with pytest.raises(GraphValidationError, match="initial source"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.self-initial-external-repeat"),
                GraphDefinitionVersion(1),
                (loop, producer),
                (
                    DirectEdge(GraphNodeId("loop"), GraphNodeId("producer")),
                    ConditionalEdge(GraphNodeId("producer"), GraphRouteId("repeat"), GraphNodeId("loop")),
                    ConditionalEdge(GraphNodeId("producer"), GraphRouteId("done"), END),
                ),
                (GraphNodeId("loop"),),
                normalize_graph_output_declarations({"result": Graph.node_output("producer", "value")}),
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


def test_compiler_accepts_feedback_inside_a_nested_graph() -> None:
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

    compiled = compile_graph(parent)

    child_compiled = compiled.nested_graphs[GraphNodeId("nested")]
    assert len(child_compiled.transition.activation_rules.entries) == 1
    rule = child_compiled.transition.activation_rules.entries[0]
    assert rule.target == GraphNodeId("loop")
    assert rule.repeat_gates == (((GraphNodeId("loop"), frozenset({GraphRouteId("continue")})),),)


def test_compiler_builds_multiple_feedback_rules_with_fixed_repeat_sources() -> None:
    left_seed = Graph.graph_input("left_seed", int)
    right_seed = Graph.graph_input("right_seed", int)
    compiled = compile_graph(
        _feedback_definition(
            inputs={
                "left": FeedbackInputBinding(left_seed, Graph.node_output("loop", "left")),
                "right": FeedbackInputBinding(right_seed, Graph.node_output("loop", "right")),
            },
            node_outputs={"left": int, "right": int},
            outputs=normalize_graph_output_declarations({"result": Graph.node_output("loop", "left")}),
        )
    )

    rules = compiled.transition.activation_rules.entries
    assert tuple(rule.input_name for rule in rules) == ("left", "right")
    assert tuple(rule.repeat.output_name for rule in rules) == ("left", "right")
    assert all(
        rule.repeat_gates == (((GraphNodeId("loop"), frozenset({GraphRouteId("continue")})),),) for rule in rules
    )
    assert compiled.transition.activation_rules.for_target(GraphNodeId("loop")) == rules
    bindings = compiled.transition.materializations[GraphNodeId("loop")].bindings.entries
    assert tuple(binding.source for binding in bindings) == rules


def test_compiler_validates_every_feedback_repeat_binding() -> None:
    left_seed = Graph.graph_input("left_seed", int)
    right_seed = Graph.graph_input("right_seed", int)

    with pytest.raises(GraphValidationError, match="unknown output port"):
        compile_graph(
            _feedback_definition(
                inputs={
                    "left": FeedbackInputBinding(left_seed, Graph.node_output("loop", "left")),
                    "right": FeedbackInputBinding(right_seed, Graph.node_output("loop", "missing")),
                },
                node_outputs={"left": int, "right": int},
                outputs=normalize_graph_output_declarations({"result": Graph.node_output("loop", "left")}),
            )
        )


def test_compiler_requires_graph_output_to_match_one_of_the_repeat_sources() -> None:
    left_seed = Graph.graph_input("left_seed", int)
    right_seed = Graph.graph_input("right_seed", int)

    with pytest.raises(GraphValidationError, match="repeat output"):
        compile_graph(
            _feedback_definition(
                inputs={
                    "left": FeedbackInputBinding(left_seed, Graph.node_output("loop", "left")),
                    "right": FeedbackInputBinding(right_seed, Graph.node_output("loop", "right")),
                },
                node_outputs={"left": int, "right": int, "other": int},
                outputs=normalize_graph_output_declarations({"result": Graph.node_output("loop", "other")}),
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


def test_compiler_builds_a_multi_node_feedback_cycle_with_one_fixed_repeat_source() -> None:
    seed = Graph.graph_input("seed", int)
    a = _node(
        "a",
        {"value": FeedbackInputBinding(seed, Graph.node_output("c", "value"))},
        {"value": int},
    )
    b = _node("b", {"value": Graph.node_output("a", "value")}, {"value": int})
    c = _node("c", {"value": Graph.node_output("b", "value")}, {"value": int})
    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.multi-node"),
            GraphDefinitionVersion(1),
            (a, b, c),
            (
                DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
                DirectEdge(GraphNodeId("b"), GraphNodeId("c")),
                ConditionalEdge(GraphNodeId("c"), GraphRouteId("again"), GraphNodeId("a")),
                ConditionalEdge(GraphNodeId("c"), GraphRouteId("done"), END),
            ),
            (GraphNodeId("a"),),
            normalize_graph_output_declarations({"result": Graph.node_output("c", "value")}),
        )
    )

    rule = compiled.transition.activation_rules.entries[0]
    assert rule.target == GraphNodeId("a")
    assert isinstance(rule.initial, GraphInputPort)
    assert rule.initial.name == "seed"
    assert rule.repeat.node_id == GraphNodeId("c")
    assert rule.initial_gates == ()
    assert rule.repeat_gates == (((GraphNodeId("c"), frozenset({GraphRouteId("again")})),),)
    assert rule.repeat_selection.kind is PublicationSelectionKind.RELATIVE
    assert rule.repeat_selection.superstep == 1


def test_compiler_builds_feedback_with_a_node_output_initial_source() -> None:
    seed = Graph.graph_input("seed", int)
    source = _node("source", {"value": seed}, {"value": int})
    target = _node(
        "target",
        {
            "value": FeedbackInputBinding(
                Graph.node_output("source", "value"),
                Graph.node_output("worker", "value"),
            )
        },
        {"value": int},
    )
    worker = _node("worker", {"value": Graph.node_output("target", "value")}, {"value": int})
    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.node-seed"),
            GraphDefinitionVersion(1),
            (source, target, worker),
            (
                DirectEdge(GraphNodeId("source"), GraphNodeId("target")),
                DirectEdge(GraphNodeId("target"), GraphNodeId("worker")),
                ConditionalEdge(GraphNodeId("worker"), GraphRouteId("again"), GraphNodeId("target")),
                ConditionalEdge(GraphNodeId("worker"), GraphRouteId("done"), END),
            ),
            (),
            normalize_graph_output_declarations({"result": Graph.node_output("worker", "value")}),
        )
    )

    rule = compiled.transition.activation_rules.entries[0]
    assert isinstance(rule.initial, NodeOutputPort)
    assert rule.initial.node_id == GraphNodeId("source")
    assert rule.initial_gates == (((GraphNodeId("source"), frozenset({None})),),)
    assert rule.repeat_gates == (((GraphNodeId("worker"), frozenset({GraphRouteId("again")})),),)
    assert rule.initial_selection is not None
    assert rule.initial_selection.kind is PublicationSelectionKind.ABSOLUTE
    assert rule.initial_selection.superstep == 0


def test_compiler_rejects_initial_and_repeat_sources_sharing_one_gate() -> None:
    initial = _node("initial", {}, {"value": int})
    target = _node(
        "target",
        {
            "value": FeedbackInputBinding(
                Graph.node_output("initial", "value"),
                Graph.node_output("initial", "value"),
            )
        },
        {"value": int},
    )

    with pytest.raises(GraphValidationError, match="share an activation gate"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.shared-gate"),
                GraphDefinitionVersion(1),
                (initial, target),
                (
                    DirectEdge(GraphNodeId("initial"), GraphNodeId("target")),
                    DirectEdge(GraphNodeId("target"), GraphNodeId("initial")),
                    ConditionalEdge(GraphNodeId("target"), GraphRouteId("done"), END),
                ),
                (GraphNodeId("initial"),),
                normalize_graph_output_declarations({"result": Graph.node_output("target", "value")}),
            )
        )


def test_compiler_rejects_a_join_gate_that_contains_initial_and_repeat_sources() -> None:
    initial = _node("initial", {}, {"value": int})
    repeat = _node("repeat", {}, {"value": int})
    target = _node(
        "target",
        {
            "value": FeedbackInputBinding(
                Graph.node_output("initial", "value"),
                Graph.node_output("repeat", "value"),
            )
        },
        {"value": int},
    )

    with pytest.raises(GraphValidationError):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.join-shared-gate"),
                GraphDefinitionVersion(1),
                (initial, repeat, target),
                (
                    DirectEdge(GraphNodeId("initial"), GraphNodeId("target")),
                    DirectEdge(GraphNodeId("target"), GraphNodeId("repeat")),
                    JoinEdge((GraphNodeId("initial"), GraphNodeId("repeat")), GraphNodeId("target")),
                    ConditionalEdge(GraphNodeId("target"), GraphRouteId("done"), END),
                ),
                (),
                normalize_graph_output_declarations({"result": Graph.node_output("target", "value")}),
            )
        )


def test_compiler_rejects_a_repeatable_initial_gate_as_a_temporal_proof() -> None:
    source = _node("source", {}, {"value": int})
    target = _node(
        "target",
        {
            "value": FeedbackInputBinding(
                Graph.node_output("source", "value"),
                Graph.node_output("worker", "value"),
            )
        },
        {"value": int},
    )
    worker = _node("worker", {}, {"value": int})

    with pytest.raises(GraphValidationError, match="without a Join"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.repeatable-initial"),
                GraphDefinitionVersion(1),
                (source, target, worker),
                (
                    DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
                    DirectEdge(GraphNodeId("source"), GraphNodeId("target")),
                    DirectEdge(GraphNodeId("target"), GraphNodeId("worker")),
                    DirectEdge(GraphNodeId("worker"), GraphNodeId("target")),
                    DirectEdge(GraphNodeId("target"), END),
                ),
                (GraphNodeId("source"),),
                normalize_graph_output_declarations({}),
            )
        )


def test_compiler_accepts_mutually_exclusive_repeat_routes_to_one_feedback_target() -> None:
    seed = Graph.graph_input("seed", int)
    target = _node(
        "target",
        {"value": FeedbackInputBinding(seed, Graph.node_output("branch", "value"))},
        {"value": int},
    )
    branch = _node("branch", {"value": Graph.node_output("target", "value")}, {"value": int})
    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.mutually-exclusive"),
            GraphDefinitionVersion(1),
            (target, branch),
            (
                ConditionalEdge(GraphNodeId("target"), GraphRouteId("run"), GraphNodeId("branch")),
                ConditionalEdge(GraphNodeId("branch"), GraphRouteId("left"), GraphNodeId("target")),
                ConditionalEdge(GraphNodeId("branch"), GraphRouteId("right"), GraphNodeId("target")),
                ConditionalEdge(GraphNodeId("branch"), GraphRouteId("done"), END),
            ),
            (GraphNodeId("target"),),
            normalize_graph_output_declarations({"result": Graph.node_output("branch", "value")}),
        )
    )

    rule = compiled.transition.activation_rules.entries[0]
    assert rule.repeat_gates == (
        ((GraphNodeId("branch"), frozenset({GraphRouteId("left")})),),
        ((GraphNodeId("branch"), frozenset({GraphRouteId("right")})),),
    )


def test_compiler_rejects_coexisting_direct_and_conditional_feedback_gates_without_join() -> None:
    seed = Graph.graph_input("seed", int)
    target = _node(
        "target",
        {"value": FeedbackInputBinding(seed, Graph.node_output("branch", "value"))},
        {"value": int},
    )
    branch = _node("branch", {"value": Graph.node_output("target", "value")}, {"value": int})

    with pytest.raises(GraphValidationError, match="without a Join"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("feedback.ambiguous-gates"),
                GraphDefinitionVersion(1),
                (target, branch),
                (
                    DirectEdge(GraphNodeId("target"), GraphNodeId("branch")),
                    DirectEdge(GraphNodeId("branch"), GraphNodeId("target")),
                    ConditionalEdge(GraphNodeId("branch"), GraphRouteId("again"), GraphNodeId("target")),
                    ConditionalEdge(GraphNodeId("branch"), GraphRouteId("done"), END),
                ),
                (GraphNodeId("target"),),
                normalize_graph_output_declarations({"result": Graph.node_output("branch", "value")}),
            )
        )


def test_compiler_accepts_one_shot_initial_before_repeat_source_through_join() -> None:
    seed = Graph.graph_input("seed", int)
    source = _node("source", {"value": seed}, {"value": int})
    target = _node(
        "target",
        {
            "value": FeedbackInputBinding(
                Graph.node_output("source", "value"),
                Graph.node_output("repeat", "value"),
            )
        },
        {"value": int},
    )
    fanout = _node("fanout", {}, {"value": int})
    left = _node("left", {}, {"value": int})
    right = _node("right", {}, {"value": int})
    joined = _node("joined", {}, {"value": int})
    repeat = _node("repeat", {}, {"value": int})

    compiled = compile_graph(
        GraphDefinition(
            GraphDefinitionId("feedback.initial-before-join"),
            GraphDefinitionVersion(1),
            (source, target, fanout, left, right, joined, repeat),
            (
                DirectEdge(GraphNodeId("source"), GraphNodeId("target")),
                DirectEdge(GraphNodeId("target"), GraphNodeId("fanout")),
                DirectEdge(GraphNodeId("fanout"), GraphNodeId("left")),
                DirectEdge(GraphNodeId("fanout"), GraphNodeId("right")),
                JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
                DirectEdge(GraphNodeId("joined"), GraphNodeId("repeat")),
                ConditionalEdge(GraphNodeId("repeat"), GraphRouteId("again"), GraphNodeId("target")),
                ConditionalEdge(GraphNodeId("repeat"), GraphRouteId("done"), END),
            ),
            (),
            normalize_graph_output_declarations({"result": Graph.node_output("repeat", "value")}),
        )
    )

    rule = compiled.transition.activation_rules.entries[0]
    assert rule.initial_selection is not None
    assert rule.initial_selection.kind is PublicationSelectionKind.ABSOLUTE
    assert rule.initial_selection.superstep == 0


def test_compiler_rejects_a_graph_without_an_entry() -> None:
    a = _node("a", {}, {"value": int})
    b = _node("b", {}, {"value": int})

    with pytest.raises(GraphValidationError, match="requires at least one"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("compiler.no-entry"),
                GraphDefinitionVersion(1),
                (a, b),
                (DirectEdge(GraphNodeId("a"), GraphNodeId("b")), DirectEdge(GraphNodeId("b"), GraphNodeId("a"))),
                (),
                normalize_graph_output_declarations({}),
            )
        )


def test_compiler_rejects_unreachable_nodes() -> None:
    a = _node("a", {}, {"value": int})
    b = _node("b", {}, {"value": int})
    c = _node("c", {}, {"value": int})

    with pytest.raises(GraphValidationError, match="unreachable nodes"):
        compile_graph(
            GraphDefinition(
                GraphDefinitionId("compiler.unreachable"),
                GraphDefinitionVersion(1),
                (a, b, c),
                (
                    DirectEdge(GraphNodeId("b"), GraphNodeId("a")),
                    DirectEdge(GraphNodeId("b"), GraphNodeId("c")),
                    DirectEdge(GraphNodeId("c"), GraphNodeId("b")),
                ),
                (GraphNodeId("a"),),
                normalize_graph_output_declarations({}),
            )
        )

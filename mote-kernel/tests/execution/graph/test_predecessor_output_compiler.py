from collections.abc import Mapping
from typing import TypeAlias

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    CompiledPredecessorInput,
    GraphInputRef,
    NodeOutputRef,
    PredecessorOutputRef,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId, GraphRouteId

Value: TypeAlias = str | int
InputRef: TypeAlias = GraphInputRef[Value] | NodeOutputRef | PredecessorOutputRef


async def identity(values: Graph.Values[Value], /) -> Graph.Values[Value]:
    return values


def node(
    node_id: str,
    *,
    inputs: Mapping[str, InputRef] | None = None,
    outputs: Mapping[str, type[Value]] | None = None,
) -> CallableNodeDefinition[Value]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        identity,
        normalize_input_bindings({} if inputs is None else inputs),
        normalize_output_declarations({} if outputs is None else outputs),
    )


def branch_definition(
    *,
    left_outputs: Mapping[str, type[Value]] | None = None,
    right_outputs: Mapping[str, type[Value]] | None = None,
) -> GraphDefinition[Value]:
    return GraphDefinition(
        GraphDefinitionId("predecessor.compiler"),
        GraphDefinitionVersion(1),
        (
            node("decision"),
            node("left", outputs={"hook_request": str} if left_outputs is None else left_outputs),
            node("right", outputs={"hook_request": str} if right_outputs is None else right_outputs),
            node("hook", inputs={"request": Graph.node_output("hook_request")}),
        ),
        (
            ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
            ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
            DirectEdge(GraphNodeId("left"), GraphNodeId("hook")),
            DirectEdge(GraphNodeId("right"), GraphNodeId("hook")),
            DirectEdge(GraphNodeId("hook"), END),
        ),
        (),
        normalize_graph_output_declarations({}),
    )


def test_compiler_resolves_every_mutually_exclusive_predecessor_without_a_public_source_map() -> None:
    compiled = compile_graph(branch_definition())

    binding = compiled.transition.materializations[GraphNodeId("hook")].bindings.entries[0]
    assert binding.destination.local_name == "request"
    assert binding.descriptor.value_type is str
    assert binding.publication is None
    assert isinstance(binding.source, CompiledPredecessorInput)
    assert binding.source.target == GraphNodeId("hook")
    assert binding.source.input_name == "request"
    assert tuple((source.node_id, source.output_name) for source in binding.source.sources) == (
        (GraphNodeId("left"), "hook_request"),
        (GraphNodeId("right"), "hook_request"),
    )


def test_compiler_accepts_an_explicit_initializer_for_a_self_loop() -> None:
    definition = GraphDefinition(
        GraphDefinitionId("predecessor.self-loop"),
        GraphDefinitionVersion(1),
        (
            node("initialize", outputs={"value": int}),
            node(
                "loop",
                inputs={"value": Graph.node_output("value")},
                outputs={"value": int},
            ),
        ),
        (
            DirectEdge(GraphNodeId("initialize"), GraphNodeId("loop")),
            ConditionalEdge(GraphNodeId("loop"), GraphRouteId("again"), GraphNodeId("loop")),
            ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), END),
        ),
        (),
        normalize_graph_output_declarations({"value": Graph.node_output("loop", "value")}),
    )

    compiled = compile_graph(definition)

    binding = compiled.transition.materializations[GraphNodeId("loop")].bindings.entries[0]
    assert isinstance(binding.source, CompiledPredecessorInput)
    assert tuple(source.node_id for source in binding.source.sources) == (
        GraphNodeId("initialize"),
        GraphNodeId("loop"),
    )


def test_compiler_rejects_a_predecessor_bound_start_target() -> None:
    definition = GraphDefinition(
        GraphDefinitionId("predecessor.start"),
        GraphDefinitionVersion(1),
        (
            node("source", outputs={"value": str}),
            node("target", inputs={"value": Graph.node_output("value")}),
        ),
        (DirectEdge(GraphNodeId("source"), GraphNodeId("target")),),
        (GraphNodeId("target"),),
        normalize_graph_output_declarations({}),
    )

    with pytest.raises(GraphValidationError, match="cannot be activated from START"):
        compile_graph(definition)


def test_compiler_rejects_a_join_as_an_implicit_predecessor() -> None:
    definition = GraphDefinition(
        GraphDefinitionId("predecessor.join"),
        GraphDefinitionVersion(1),
        (
            node("left", outputs={"value": str}),
            node("right", outputs={"value": str}),
            node("target", inputs={"value": Graph.node_output("value")}),
        ),
        (
            JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("target")),
            DirectEdge(GraphNodeId("target"), END),
        ),
        (),
        normalize_graph_output_declarations({}),
    )

    with pytest.raises(GraphValidationError, match="cannot be activated by a Join"):
        compile_graph(definition)


def test_compiler_rejects_a_missing_output_on_any_possible_predecessor() -> None:
    with pytest.raises(GraphValidationError, match="unknown output port 'hook_request'"):
        compile_graph(branch_definition(right_outputs={}))


def test_compiler_rejects_conflicting_exact_predecessor_output_types() -> None:
    with pytest.raises(GraphValidationError, match="conflicting exact output types"):
        compile_graph(branch_definition(right_outputs={"hook_request": int}))


def test_independent_predecessors_still_require_an_explicit_join_before_input_resolution() -> None:
    definition = GraphDefinition(
        GraphDefinitionId("predecessor.concurrent"),
        GraphDefinitionVersion(1),
        (
            node("left", outputs={"value": str}),
            node("right", outputs={"value": str}),
            node("target", inputs={"value": Graph.node_output("value")}),
        ),
        (
            DirectEdge(GraphNodeId("left"), GraphNodeId("target")),
            DirectEdge(GraphNodeId("right"), GraphNodeId("target")),
            DirectEdge(GraphNodeId("target"), END),
        ),
        (),
        normalize_graph_output_declarations({}),
    )

    with pytest.raises(GraphValidationError, match="multiple activation gates without an explicit Join"):
        compile_graph(definition)


def test_same_predecessor_routes_share_one_causal_output_source() -> None:
    definition = GraphDefinition(
        GraphDefinitionId("predecessor.same-source-routes"),
        GraphDefinitionVersion(1),
        (
            node("initialize", outputs={"value": int}),
            node(
                "target",
                inputs={"value": Graph.node_output("value")},
                outputs={"value": int},
            ),
            node(
                "branch",
                inputs={"value": Graph.node_output("target", "value")},
                outputs={"value": int},
            ),
        ),
        (
            DirectEdge(GraphNodeId("initialize"), GraphNodeId("target")),
            DirectEdge(GraphNodeId("target"), GraphNodeId("branch")),
            ConditionalEdge(GraphNodeId("branch"), GraphRouteId("left"), GraphNodeId("target")),
            ConditionalEdge(GraphNodeId("branch"), GraphRouteId("right"), GraphNodeId("target")),
            ConditionalEdge(GraphNodeId("branch"), GraphRouteId("done"), END),
        ),
        (),
        normalize_graph_output_declarations({"value": Graph.node_output("branch", "value")}),
    )

    compiled = compile_graph(definition)

    binding = compiled.transition.materializations[GraphNodeId("target")].bindings.entries[0]
    assert isinstance(binding.source, CompiledPredecessorInput)
    assert tuple(source.node_id for source in binding.source.sources) == (
        GraphNodeId("branch"),
        GraphNodeId("initialize"),
    )


def test_nested_graph_input_can_bind_the_actual_parent_predecessor() -> None:
    child = GraphDefinition(
        GraphDefinitionId("predecessor.child"),
        GraphDefinitionVersion(1),
        (
            node(
                "leaf",
                inputs={"hook_request": Graph.graph_input("hook_request", str)},
                outputs={"hook_request": str},
            ),
        ),
        (),
        (),
        normalize_graph_output_declarations({"hook_request": Graph.node_output("leaf", "hook_request")}),
    )
    parent = branch_definition()
    nested = NestedGraphNodeDefinition(
        GraphNodeId("hook"),
        child,
        normalize_input_bindings({"hook_request": Graph.node_output("hook_request")}),
    )
    definition = GraphDefinition(
        GraphDefinitionId("predecessor.parent"),
        GraphDefinitionVersion(1),
        (*parent.nodes[:3], nested),
        parent.edges,
        parent.entries,
        parent.outputs,
    )

    compiled = compile_graph(definition)

    binding = compiled.transition.materializations[GraphNodeId("hook")].bindings.entries[0]
    assert isinstance(binding.source, CompiledPredecessorInput)
    assert binding.descriptor.value_type is str
    assert tuple(source.node_id for source in binding.source.sources) == (
        GraphNodeId("left"),
        GraphNodeId("right"),
    )

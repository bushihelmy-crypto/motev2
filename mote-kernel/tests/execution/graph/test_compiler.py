from dataclasses import replace

import pytest
from tests.execution.graph.factories import compiled_join, graph, node

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import GraphNodeId, GraphRouteId


def test_compilation_never_invokes_nodes() -> None:
    calls = 0

    async def must_not_run(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        raise AssertionError(values)

    definition = graph(nodes=(replace(node("a"), operation=must_not_run),))
    compiled = compile_graph(definition)

    assert calls == 0
    compiled_node = compiled.nodes[GraphNodeId("a")]
    assert isinstance(compiled_node, CallableNodeDefinition)
    assert compiled_node.operation is must_not_run


def test_compile_indexes_conditional_routes_and_joins() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d"), node("e"), node("f"), node("g")),
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("e")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("d")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("f")),
            JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("g")),
        ),
    )
    compiled = compile_graph(definition)

    assert compiled.transition.direct_targets[GraphNodeId("a")] == (GraphNodeId("e"),)
    assert compiled.transition.conditional_targets[GraphNodeId("a")][GraphRouteId("left")] == GraphNodeId("d")
    assert compiled.transition.conditional_targets[GraphNodeId("a")][GraphRouteId("right")] == GraphNodeId("f")
    expected_join = compiled_join(("b", "c"), "g")
    assert compiled.transition.joins_by_source[GraphNodeId("b")] == (expected_join,)
    assert compiled.transition.joins_by_source[GraphNodeId("c")] == (expected_join,)


def test_join_to_end_preserves_its_runtime_barrier() -> None:
    definition = graph(
        nodes=(node("a"), node("b")),
        edges=(JoinEdge((GraphNodeId("a"), GraphNodeId("b")), END),),
    )

    compiled = compile_graph(definition)

    expected_join = compiled_join(("a", "b"), END)
    assert compiled.transition.joins_by_source[GraphNodeId("a")] == (expected_join,)
    assert compiled.transition.joins_by_source[GraphNodeId("b")] == (expected_join,)


def test_cyclic_join_compiles_when_every_source_shares_one_activation_cohort() -> None:
    definition = graph(
        nodes=(node("tick"), node("left"), node("right"), node("joined")),
        edges=(
            DirectEdge(GraphNodeId("tick"), GraphNodeId("left")),
            DirectEdge(GraphNodeId("tick"), GraphNodeId("right")),
            JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
            ConditionalEdge(GraphNodeId("joined"), GraphRouteId("again"), GraphNodeId("tick")),
            ConditionalEdge(GraphNodeId("joined"), GraphRouteId("done"), END),
        ),
        entries=(GraphNodeId("tick"),),
    )

    compiled = compile_graph(definition)
    expected = compiled_join(("left", "right"), "joined")

    assert compiled.transition.joins_by_source[GraphNodeId("left")] == (expected,)
    assert compiled.transition.joins_by_source[GraphNodeId("right")] == (expected,)


def test_cyclic_join_rejects_sources_from_different_activation_cohorts() -> None:
    definition = graph(
        nodes=(node("tick"), node("left"), node("middle"), node("right"), node("joined")),
        edges=(
            DirectEdge(GraphNodeId("tick"), GraphNodeId("left")),
            DirectEdge(GraphNodeId("tick"), GraphNodeId("middle")),
            DirectEdge(GraphNodeId("middle"), GraphNodeId("right")),
            JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
            ConditionalEdge(GraphNodeId("joined"), GraphRouteId("again"), GraphNodeId("tick")),
            ConditionalEdge(GraphNodeId("joined"), GraphRouteId("done"), END),
        ),
        entries=(GraphNodeId("tick"),),
    )

    with pytest.raises(GraphValidationError, match="no provable occurrence identity"):
        compile_graph(definition)


def test_join_rejects_mutually_exclusive_cohorts_without_one_absolute_coordinate() -> None:
    definition = graph(
        nodes=tuple(node(node_id) for node_id in ("decision", "short", "middle", "long", "left", "right", "joined")),
        edges=(
            ConditionalEdge(GraphNodeId("decision"), GraphRouteId("short"), GraphNodeId("short")),
            ConditionalEdge(GraphNodeId("decision"), GraphRouteId("long"), GraphNodeId("middle")),
            DirectEdge(GraphNodeId("middle"), GraphNodeId("long")),
            DirectEdge(GraphNodeId("short"), GraphNodeId("left")),
            DirectEdge(GraphNodeId("short"), GraphNodeId("right")),
            DirectEdge(GraphNodeId("long"), GraphNodeId("left")),
            DirectEdge(GraphNodeId("long"), GraphNodeId("right")),
            JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
        ),
    )

    with pytest.raises(GraphValidationError, match="no unique occurrence coordinate"):
        compile_graph(definition)


def test_control_cycles_without_a_successful_exit_are_rejected() -> None:
    cycle = graph(
        nodes=(node("a"), node("b")),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")), DirectEdge(GraphNodeId("b"), GraphNodeId("a"))),
        entries=(GraphNodeId("a"),),
    )
    self_loop = graph(
        nodes=(node("a"),),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("a")),),
        entries=(GraphNodeId("a"),),
    )

    with pytest.raises(GraphValidationError, match="no statically reachable successful exit"):
        compile_graph(cycle)
    with pytest.raises(GraphValidationError, match="no statically reachable successful exit"):
        compile_graph(self_loop)


@pytest.mark.parametrize(
    "definition",
    [
        graph(
            nodes=(node("a"), node("b"), node("joined")),
            edges=(
                DirectEdge(GraphNodeId("a"), GraphNodeId("a")),
                DirectEdge(GraphNodeId("a"), END),
                JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("joined")),
            ),
            entries=(GraphNodeId("a"),),
        ),
        graph(
            nodes=(node("loop"), node("repeated"), node("fixed"), node("joined")),
            edges=(
                DirectEdge(GraphNodeId("loop"), GraphNodeId("loop")),
                DirectEdge(GraphNodeId("loop"), GraphNodeId("repeated")),
                DirectEdge(GraphNodeId("loop"), END),
                JoinEdge(
                    (GraphNodeId("repeated"), GraphNodeId("fixed")),
                    GraphNodeId("joined"),
                ),
            ),
            entries=(GraphNodeId("loop"),),
        ),
    ],
)
def test_join_with_a_repeatable_source_requires_occurrence_identity(definition: GraphDefinition[str]) -> None:
    with pytest.raises(GraphValidationError, match="occurrence identity"):
        compile_graph(definition)


def test_multiple_entries_and_direct_fan_out_are_sorted() -> None:
    definition = graph(
        nodes=(node("d"), node("c"), node("b"), node("a")),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("d")), DirectEdge(GraphNodeId("a"), GraphNodeId("c"))),
    )
    compiled = compile_graph(definition)

    assert compiled.transition.entries == (GraphNodeId("a"), GraphNodeId("b"))
    assert compiled.transition.direct_targets[GraphNodeId("a")] == (GraphNodeId("c"), GraphNodeId("d"))


def test_declaration_order_does_not_change_compiled_indexes() -> None:
    first = graph(
        nodes=(node("a"), node("b"), node("c"), node("d"), node("e"), node("f"), node("g")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("f")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("e")),
            JoinEdge((GraphNodeId("c"), GraphNodeId("b")), GraphNodeId("g")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("d")),
        ),
    )
    second = graph(
        nodes=(node("g"), node("f"), node("e"), node("d"), node("c"), node("b"), node("a")),
        edges=(
            JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("g")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("d")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("e")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("f")),
        ),
    )

    first_compiled = compile_graph(first)
    second_compiled = compile_graph(second)

    assert tuple(first_compiled.nodes) == tuple(second_compiled.nodes)
    assert tuple(first_compiled.transition.direct_targets) == tuple(second_compiled.transition.direct_targets)
    assert tuple(first_compiled.transition.conditional_targets) == tuple(second_compiled.transition.conditional_targets)
    assert tuple(first_compiled.transition.joins_by_source) == tuple(second_compiled.transition.joins_by_source)
    assert dict(first_compiled.transition.direct_targets) == dict(second_compiled.transition.direct_targets)
    assert {source: dict(routes) for source, routes in first_compiled.transition.conditional_targets.items()} == {
        source: dict(routes) for source, routes in second_compiled.transition.conditional_targets.items()
    }
    assert dict(first_compiled.transition.joins_by_source) == dict(second_compiled.transition.joins_by_source)


def test_direct_and_conditional_edges_may_share_a_source() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("optional"), GraphNodeId("c")),
        ),
    )
    compiled = compile_graph(definition)

    assert compiled.transition.direct_targets[GraphNodeId("a")] == (GraphNodeId("b"),)
    assert compiled.transition.conditional_targets[GraphNodeId("a")][GraphRouteId("optional")] == GraphNodeId("c")


def test_multiple_routes_may_share_a_target_and_identity_across_sources() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("first"), GraphNodeId("c")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("second"), GraphNodeId("c")),
            ConditionalEdge(GraphNodeId("b"), GraphRouteId("first"), GraphNodeId("c")),
        ),
    )
    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        compile_graph(definition)


def test_compiling_the_same_definition_is_idempotent() -> None:
    definition = graph(
        nodes=(node("a"), node("b")),
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("b"), GraphRouteId("finish"), END),
        ),
    )

    first = compile_graph(definition)
    second = compile_graph(definition)

    assert first == second
    assert first is not second


def test_compilation_uses_declared_resource_tuple_order_for_requirements() -> None:
    definition = graph(
        nodes=(replace(node("a"), resources=(ResourceId("database"), ResourceId("file"))),),
        resources=(
            ResourceDefinition(ResourceId("file")),
            ResourceDefinition(ResourceId("database")),
        ),
    )

    compiled = compile_graph(definition)
    compiled_node = compiled.nodes[GraphNodeId("a")]

    assert isinstance(compiled_node, CallableNodeDefinition)
    assert compiled.transition.resource_order == (ResourceId("file"), ResourceId("database"))
    assert compiled_node.resources == (ResourceId("file"), ResourceId("database"))
    # The lookup map is key-sorted; runtime order comes from the declaration tuple above.
    assert tuple(compiled.resources) == (ResourceId("database"), ResourceId("file"))

from tests.execution.graph.factories import graph, node

from mote_kernel.execution.graph import (
    END,
    ConditionalEdge,
    DirectEdge,
    GraphNodeId,
    GraphRouteId,
    JoinEdge,
    NodeDefinition,
    NodeSuccess,
    compile_graph,
)
from mote_kernel.execution.resource import ResourceDefinition, ResourceId


def test_compilation_never_invokes_nodes() -> None:
    calls = 0

    async def must_not_run(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        raise AssertionError(node_input)

    definition = graph(nodes=(NodeDefinition(GraphNodeId("a"), must_not_run),))
    compiled = compile_graph(definition)

    assert calls == 0
    assert compiled.nodes[GraphNodeId("a")].node is must_not_run  # type: ignore[union-attr]


def test_compile_indexes_conditional_routes_and_joins() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("c")),
            JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("d")),
        ),
    )
    compiled = compile_graph(definition)

    assert compiled.conditional_targets[GraphNodeId("a")][GraphRouteId("left")] == GraphNodeId("b")
    assert compiled.conditional_targets[GraphNodeId("a")][GraphRouteId("right")] == GraphNodeId("c")
    expected_join = JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("d"))
    assert compiled.joins_by_source[GraphNodeId("b")] == (expected_join,)
    assert compiled.joins_by_source[GraphNodeId("c")] == (expected_join,)


def test_join_to_end_preserves_its_runtime_barrier() -> None:
    definition = graph(
        nodes=(node("a"), node("b")),
        edges=(JoinEdge((GraphNodeId("a"), GraphNodeId("b")), END),),
        entries=(GraphNodeId("a"), GraphNodeId("b")),
    )

    compiled = compile_graph(definition)

    expected_join = JoinEdge((GraphNodeId("a"), GraphNodeId("b")), END)
    assert compiled.joins_by_source[GraphNodeId("a")] == (expected_join,)
    assert compiled.joins_by_source[GraphNodeId("b")] == (expected_join,)


def test_cycles_and_self_loops_compile() -> None:
    cycle = graph(
        nodes=(node("a"), node("b")),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")), DirectEdge(GraphNodeId("b"), GraphNodeId("a"))),
    )
    self_loop = graph(nodes=(node("a"),), edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("a")),))

    assert compile_graph(cycle).direct_targets[GraphNodeId("b")] == (GraphNodeId("a"),)
    assert compile_graph(self_loop).direct_targets[GraphNodeId("a")] == (GraphNodeId("a"),)


def test_multiple_entries_and_direct_fan_out_are_sorted() -> None:
    definition = graph(
        nodes=(node("d"), node("c"), node("b"), node("a")),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("d")), DirectEdge(GraphNodeId("a"), GraphNodeId("c"))),
        entries=(GraphNodeId("b"), GraphNodeId("a")),
    )
    compiled = compile_graph(definition)

    assert compiled.entries == (GraphNodeId("a"), GraphNodeId("b"))
    assert compiled.direct_targets[GraphNodeId("a")] == (GraphNodeId("c"), GraphNodeId("d"))


def test_declaration_order_does_not_change_compiled_indexes() -> None:
    first = graph(
        nodes=(node("a"), node("b"), node("c"), node("d")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("c")),
            JoinEdge((GraphNodeId("c"), GraphNodeId("b")), GraphNodeId("d")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("b")),
        ),
    )
    second = graph(
        nodes=(node("d"), node("c"), node("b"), node("a")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("b")),
            JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("d")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("c")),
        ),
    )

    first_compiled = compile_graph(first)
    second_compiled = compile_graph(second)

    assert tuple(first_compiled.nodes) == tuple(second_compiled.nodes)
    assert tuple(first_compiled.direct_targets) == tuple(second_compiled.direct_targets)
    assert tuple(first_compiled.conditional_targets) == tuple(second_compiled.conditional_targets)
    assert tuple(first_compiled.joins_by_source) == tuple(second_compiled.joins_by_source)
    assert dict(first_compiled.direct_targets) == dict(second_compiled.direct_targets)
    assert {source: dict(routes) for source, routes in first_compiled.conditional_targets.items()} == {
        source: dict(routes) for source, routes in second_compiled.conditional_targets.items()
    }
    assert dict(first_compiled.joins_by_source) == dict(second_compiled.joins_by_source)


def test_direct_and_conditional_edges_may_share_a_source() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("optional"), GraphNodeId("c")),
        ),
    )
    compiled = compile_graph(definition)

    assert compiled.direct_targets[GraphNodeId("a")] == (GraphNodeId("b"),)
    assert compiled.conditional_targets[GraphNodeId("a")][GraphRouteId("optional")] == GraphNodeId("c")


def test_multiple_routes_may_share_a_target_and_identity_across_sources() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("first"), GraphNodeId("c")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("second"), GraphNodeId("c")),
            ConditionalEdge(GraphNodeId("b"), GraphRouteId("first"), GraphNodeId("c")),
        ),
        entries=(GraphNodeId("a"), GraphNodeId("b")),
    )
    compiled = compile_graph(definition)

    assert tuple(compiled.conditional_targets[GraphNodeId("a")]) == (GraphRouteId("first"), GraphRouteId("second"))
    assert compiled.conditional_targets[GraphNodeId("b")][GraphRouteId("first")] == GraphNodeId("c")


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


def test_compilation_normalizes_node_requirements_by_graph_resource_order() -> None:
    definition = graph(
        nodes=(
            NodeDefinition(
                GraphNodeId("a"),
                node("a").node,
                (ResourceId("database"), ResourceId("file")),
            ),
        ),
        resources=(
            ResourceDefinition(ResourceId("database"), 20),
            ResourceDefinition(ResourceId("file"), 10),
        ),
    )

    compiled = compile_graph(definition)
    compiled_node = compiled.nodes[GraphNodeId("a")]

    assert isinstance(compiled_node, NodeDefinition)
    assert compiled.resource_order == (ResourceId("file"), ResourceId("database"))
    assert compiled_node.resources == (ResourceId("file"), ResourceId("database"))
    assert tuple(compiled.resources) == (ResourceId("database"), ResourceId("file"))

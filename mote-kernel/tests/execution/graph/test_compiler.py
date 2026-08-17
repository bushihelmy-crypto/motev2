from dataclasses import replace

from tests.execution.graph.factories import graph, node

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
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
        nodes=(node("a"), node("b"), node("c"), node("d")),
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("c")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("c")),
            JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("d")),
        ),
    )
    compiled = compile_graph(definition)

    assert compiled.transition.conditional_targets[GraphNodeId("a")][GraphRouteId("left")] == GraphNodeId("b")
    assert compiled.transition.conditional_targets[GraphNodeId("a")][GraphRouteId("right")] == GraphNodeId("c")
    expected_join = JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("d"))
    assert compiled.transition.joins_by_source[GraphNodeId("b")] == (expected_join,)
    assert compiled.transition.joins_by_source[GraphNodeId("c")] == (expected_join,)


def test_join_to_end_preserves_its_runtime_barrier() -> None:
    definition = graph(
        nodes=(node("a"), node("b")),
        edges=(JoinEdge((GraphNodeId("a"), GraphNodeId("b")), END),),
    )

    compiled = compile_graph(definition)

    expected_join = JoinEdge((GraphNodeId("a"), GraphNodeId("b")), END)
    assert compiled.transition.joins_by_source[GraphNodeId("a")] == (expected_join,)
    assert compiled.transition.joins_by_source[GraphNodeId("b")] == (expected_join,)


def test_cycles_and_self_loops_compile() -> None:
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

    assert compile_graph(cycle).transition.direct_targets[GraphNodeId("b")] == (GraphNodeId("a"),)
    assert compile_graph(self_loop).transition.direct_targets[GraphNodeId("a")] == (GraphNodeId("a"),)


def test_multiple_entries_and_direct_fan_out_are_sorted() -> None:
    definition = graph(
        nodes=(node("d"), node("c"), node("b"), node("a")),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("d")), DirectEdge(GraphNodeId("a"), GraphNodeId("c"))),
    )
    compiled = compile_graph(definition)

    assert compiled.entries == (GraphNodeId("a"), GraphNodeId("b"))
    assert compiled.transition.direct_targets[GraphNodeId("a")] == (GraphNodeId("c"), GraphNodeId("d"))


def test_declaration_order_does_not_change_compiled_indexes() -> None:
    first = graph(
        nodes=(node("a"), node("b"), node("c"), node("d")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("c")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
            JoinEdge((GraphNodeId("c"), GraphNodeId("b")), GraphNodeId("d")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("c")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("b")),
        ),
    )
    second = graph(
        nodes=(node("d"), node("c"), node("b"), node("a")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("left"), GraphNodeId("b")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("c")),
            JoinEdge((GraphNodeId("b"), GraphNodeId("c")), GraphNodeId("d")),
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("right"), GraphNodeId("c")),
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
    compiled = compile_graph(definition)

    assert tuple(compiled.transition.conditional_targets[GraphNodeId("a")]) == (
        GraphRouteId("first"),
        GraphRouteId("second"),
    )
    assert compiled.transition.conditional_targets[GraphNodeId("b")][GraphRouteId("first")] == GraphNodeId("c")


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
        nodes=(replace(node("a"), resources=(ResourceId("file"), ResourceId("database"))),),
        resources=(
            ResourceDefinition(ResourceId("database"), 0),
            ResourceDefinition(ResourceId("file"), 1),
        ),
    )

    compiled = compile_graph(definition)
    compiled_node = compiled.nodes[GraphNodeId("a")]

    assert isinstance(compiled_node, CallableNodeDefinition)
    assert compiled.resource_order == (ResourceId("database"), ResourceId("file"))
    assert compiled_node.resources == (ResourceId("database"), ResourceId("file"))
    assert tuple(compiled.resources) == (ResourceId("database"), ResourceId("file"))

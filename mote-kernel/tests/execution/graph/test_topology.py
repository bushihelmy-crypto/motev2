from dataclasses import FrozenInstanceError

import pytest
from tests.execution.graph.factories import graph, node

from mote_kernel.execution.graph import ConditionalEdge, DirectEdge, GraphNodeId, GraphRouteId, compile_graph


def test_compiled_indexes_are_total_and_sorted_for_every_node() -> None:
    definition = graph(
        nodes=(node("c"), node("a"), node("b")),
        edges=(DirectEdge(GraphNodeId("a"), GraphNodeId("b")),),
        entries=(GraphNodeId("a"), GraphNodeId("c")),
    )
    compiled = compile_graph(definition)

    expected_nodes = (GraphNodeId("a"), GraphNodeId("b"), GraphNodeId("c"))
    assert tuple(compiled.nodes) == expected_nodes
    assert tuple(compiled.direct_targets) == expected_nodes
    assert tuple(compiled.conditional_targets) == expected_nodes
    assert tuple(compiled.joins_by_source) == expected_nodes
    assert compiled.direct_targets[GraphNodeId("b")] == ()
    assert dict(compiled.conditional_targets[GraphNodeId("c")]) == {}
    assert compiled.joins_by_source[GraphNodeId("c")] == ()


def test_graph_definition_edges_and_compiled_graph_are_immutable() -> None:
    edge = DirectEdge(GraphNodeId("a"), GraphNodeId("b"))
    definition = graph(nodes=(node("a"), node("b")), edges=(edge,))
    compiled = compile_graph(definition)

    with pytest.raises(FrozenInstanceError):
        definition.entries = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        edge.target = GraphNodeId("a")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        compiled.entries = ()  # type: ignore[misc]


def test_compiled_indexes_are_deeply_immutable() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("next"), GraphNodeId("c")),
        ),
    )
    compiled = compile_graph(definition)

    with pytest.raises(TypeError):
        compiled.nodes[GraphNodeId("d")] = node("d")  # type: ignore[index]
    with pytest.raises(TypeError):
        compiled.direct_targets[GraphNodeId("a")] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        compiled.conditional_targets[GraphNodeId("a")][GraphRouteId("next")] = GraphNodeId("b")  # type: ignore[index]
    with pytest.raises(TypeError):
        compiled.joins_by_source[GraphNodeId("a")] = ()  # type: ignore[index]

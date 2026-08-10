from dataclasses import FrozenInstanceError

import pytest
from tests.execution.graph.factories import graph, node

from mote_kernel.execution.graph import ConditionalEdge, DirectEdge, NodeId, RouteId, compile_graph


def test_compiled_indexes_are_total_and_sorted_for_every_node() -> None:
    definition = graph(
        nodes=(node("c"), node("a"), node("b")),
        edges=(DirectEdge(NodeId("a"), NodeId("b")),),
        entries=(NodeId("a"), NodeId("c")),
    )
    compiled = compile_graph(definition)

    expected_nodes = (NodeId("a"), NodeId("b"), NodeId("c"))
    assert tuple(compiled.nodes) == expected_nodes
    assert tuple(compiled.direct_targets) == expected_nodes
    assert tuple(compiled.conditional_targets) == expected_nodes
    assert tuple(compiled.joins_by_source) == expected_nodes
    assert compiled.direct_targets[NodeId("b")] == ()
    assert dict(compiled.conditional_targets[NodeId("c")]) == {}
    assert compiled.joins_by_source[NodeId("c")] == ()


def test_graph_definition_edges_and_compiled_graph_are_immutable() -> None:
    edge = DirectEdge(NodeId("a"), NodeId("b"))
    definition = graph(nodes=(node("a"), node("b")), edges=(edge,))
    compiled = compile_graph(definition)

    with pytest.raises(FrozenInstanceError):
        definition.entries = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        edge.target = NodeId("a")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        compiled.entries = ()  # type: ignore[misc]


def test_compiled_indexes_are_deeply_immutable() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            DirectEdge(NodeId("a"), NodeId("b")),
            ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("c")),
        ),
    )
    compiled = compile_graph(definition)

    with pytest.raises(TypeError):
        compiled.nodes[NodeId("d")] = node("d")  # type: ignore[index]
    with pytest.raises(TypeError):
        compiled.direct_targets[NodeId("a")] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        compiled.conditional_targets[NodeId("a")][RouteId("next")] = NodeId("b")  # type: ignore[index]
    with pytest.raises(TypeError):
        compiled.joins_by_source[NodeId("a")] = ()  # type: ignore[index]

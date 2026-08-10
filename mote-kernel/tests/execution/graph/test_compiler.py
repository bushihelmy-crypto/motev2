from dataclasses import FrozenInstanceError

import pytest

from mote_kernel.execution.errors import (
    DuplicateNodeError,
    GraphValidationError,
    InvalidGraphIdentityError,
    InvalidJoinError,
    MissingEntryError,
    UnknownNodeError,
    UnreachableNodeError,
)
from mote_kernel.execution.graph import (
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    JoinEdge,
    NodeDefinition,
    NodeId,
    RouteId,
    compile_graph,
)


def identity(node_input: str) -> str:
    return node_input


def node(node_id: str) -> NodeDefinition[str, str]:
    return NodeDefinition(NodeId(node_id), identity)


def graph(
    *,
    nodes: tuple[NodeDefinition[str, str], ...],
    edges: tuple[DirectEdge | ConditionalEdge | JoinEdge, ...] = (),
    entries: tuple[NodeId, ...] = (NodeId("a"),),
    exits: tuple[NodeId, ...] = (),
) -> GraphDefinition[str, str]:
    return GraphDefinition(
        definition_id=GraphDefinitionId("test.graph"),
        version=GraphDefinitionVersion(1),
        nodes=nodes,
        edges=edges,
        entries=entries,
        exits=exits,
    )


def test_compile_sequence_builds_deterministic_immutable_indexes() -> None:
    definition = graph(
        nodes=(node("c"), node("a"), node("b")),
        edges=(DirectEdge(NodeId("b"), NodeId("c")), DirectEdge(NodeId("a"), NodeId("b"))),
        exits=(NodeId("c"),),
    )

    compiled = compile_graph(definition)

    assert compiled.entries == (NodeId("a"),)
    assert tuple(compiled.nodes) == (NodeId("c"), NodeId("a"), NodeId("b"))
    assert compiled.direct_targets[NodeId("a")] == (NodeId("b"),)
    assert compiled.direct_targets[NodeId("b")] == (NodeId("c"),)
    with pytest.raises(TypeError):
        compiled.nodes[NodeId("d")] = node("d")  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        compiled.entries = ()  # type: ignore[misc]


def test_compile_indexes_conditional_routes_and_joins() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c"), node("d")),
        edges=(
            ConditionalEdge(NodeId("a"), RouteId("left"), NodeId("b")),
            ConditionalEdge(NodeId("a"), RouteId("right"), NodeId("c")),
            JoinEdge((NodeId("b"), NodeId("c")), NodeId("d")),
        ),
        exits=(NodeId("d"),),
    )

    compiled = compile_graph(definition)

    assert compiled.conditional_targets[NodeId("a")][RouteId("left")] == NodeId("b")
    assert compiled.conditional_targets[NodeId("a")][RouteId("right")] == NodeId("c")
    expected_join = JoinEdge((NodeId("b"), NodeId("c")), NodeId("d"))
    assert compiled.joins_by_source[NodeId("b")] == (expected_join,)
    assert compiled.joins_by_source[NodeId("c")] == (expected_join,)


def test_cycles_are_valid_when_reachable() -> None:
    definition = graph(
        nodes=(node("a"), node("b")),
        edges=(DirectEdge(NodeId("a"), NodeId("b")), DirectEdge(NodeId("b"), NodeId("a"))),
    )

    compiled = compile_graph(definition)

    assert compiled.direct_targets[NodeId("b")] == (NodeId("a"),)


@pytest.mark.parametrize(
    ("definition", "error"),
    [
        (graph(nodes=(node("a"),), entries=()), MissingEntryError),
        (
            GraphDefinition(
                definition_id=GraphDefinitionId(""),
                version=GraphDefinitionVersion(1),
                nodes=(node("a"),),
                edges=(),
                entries=(NodeId("a"),),
                exits=(),
            ),
            InvalidGraphIdentityError,
        ),
        (
            GraphDefinition(
                definition_id=GraphDefinitionId("test.graph"),
                version=GraphDefinitionVersion(0),
                nodes=(node("a"),),
                edges=(),
                entries=(NodeId("a"),),
                exits=(),
            ),
            InvalidGraphIdentityError,
        ),
        (graph(nodes=(node(" a"),), entries=(NodeId(" a"),)), InvalidGraphIdentityError),
        (graph(nodes=(node("a"), node("a"))), DuplicateNodeError),
        (graph(nodes=(node("a"),), entries=(NodeId("missing"),)), UnknownNodeError),
        (graph(nodes=(node("a"),), exits=(NodeId("missing"),)), UnknownNodeError),
        (
            graph(nodes=(node("a"),), edges=(DirectEdge(NodeId("a"), NodeId("missing")),)),
            UnknownNodeError,
        ),
        (graph(nodes=(node("a"), node("b"))), UnreachableNodeError),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(JoinEdge((NodeId("a"), NodeId("a")), NodeId("b")),),
            ),
            InvalidJoinError,
        ),
        (
            graph(
                nodes=(node("a"), node("b"), node("c")),
                edges=(
                    JoinEdge((NodeId("a"), NodeId("b")), NodeId("c")),
                    JoinEdge((NodeId("b"), NodeId("a")), NodeId("c")),
                ),
            ),
            InvalidJoinError,
        ),
        (
            graph(
                nodes=(node("a"), node("b")),
                edges=(ConditionalEdge(NodeId("a"), RouteId(""), NodeId("b")),),
            ),
            InvalidGraphIdentityError,
        ),
    ],
)
def test_invalid_graphs_fail_closed(definition: GraphDefinition[str, str], error: type[GraphValidationError]) -> None:
    with pytest.raises(error):
        compile_graph(definition)


def test_duplicate_conditional_route_fails_closed() -> None:
    definition = graph(
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("b")),
            ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("c")),
        ),
    )

    with pytest.raises(GraphValidationError):
        compile_graph(definition)

import pytest
from tests.execution.graph.factories import node

from mote_kernel.execution.errors import (
    DuplicateEdgeError,
    DuplicateGraphDefinitionError,
    MissingEntryError,
    RecursiveGraphDefinitionError,
)
from mote_kernel.execution.graph import (
    ConditionalEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NestedGraphNodeDefinition,
    NodeId,
    RouteId,
    compile_graph,
)


def nested(node_id: str, graph: GraphDefinition[str, str]) -> NestedGraphNodeDefinition[str, str]:
    return NestedGraphNodeDefinition(NodeId(node_id), graph)


def single_node_graph(definition_id: str, version: int, node_id: str = "step") -> GraphDefinition[str, str]:
    return GraphDefinition(
        definition_id=GraphDefinitionId(definition_id),
        version=GraphDefinitionVersion(version),
        nodes=(node(node_id),),
        edges=(),
        entries=(NodeId(node_id),),
    )


def test_nested_graph_definition_is_preserved_and_validated() -> None:
    child = single_node_graph("child.graph", 2, "child")
    child_node = nested("nested", child)
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(child_node,),
        edges=(),
        entries=(NodeId("nested"),),
    )

    compiled = compile_graph(parent)

    assert compiled.nodes[NodeId("nested")] is child_node
    assert child_node.graph.definition_id == GraphDefinitionId("child.graph")
    assert child_node.graph.version == GraphDefinitionVersion(2)


def test_invalid_nested_graph_fails_parent_compilation() -> None:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(node("child"),),
        edges=(),
        entries=(),
    )
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("nested", child),),
        edges=(),
        entries=(NodeId("nested"),),
    )

    with pytest.raises(MissingEntryError):
        compile_graph(parent)


def test_invalid_deeply_nested_graph_fails_root_compilation() -> None:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(node("child"),),
        edges=(),
        entries=(),
    )
    middle = GraphDefinition(
        definition_id=GraphDefinitionId("middle.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("child", child),),
        edges=(),
        entries=(NodeId("child"),),
    )
    root = GraphDefinition(
        definition_id=GraphDefinitionId("root.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("middle", middle),),
        edges=(),
        entries=(NodeId("middle"),),
    )

    with pytest.raises(MissingEntryError):
        compile_graph(root)


def test_nested_graph_with_duplicate_route_fails_parent_compilation() -> None:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("b")),
            ConditionalEdge(NodeId("a"), RouteId("next"), NodeId("c")),
        ),
        entries=(NodeId("a"),),
    )
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("nested", child),),
        edges=(),
        entries=(NodeId("nested"),),
    )

    with pytest.raises(DuplicateEdgeError):
        compile_graph(parent)


def test_valid_graphs_nest_with_local_node_id_reuse() -> None:
    child = single_node_graph("child.graph", 1)
    middle = GraphDefinition(
        definition_id=GraphDefinitionId("middle.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("step", child),),
        edges=(),
        entries=(NodeId("step"),),
    )
    root = GraphDefinition(
        definition_id=GraphDefinitionId("root.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("step", middle),),
        edges=(),
        entries=(NodeId("step"),),
    )

    root_node = compile_graph(root).nodes[NodeId("step")]

    assert isinstance(root_node, NestedGraphNodeDefinition)
    middle_node = root_node.graph.nodes[0]
    assert isinstance(middle_node, NestedGraphNodeDefinition)
    assert middle_node.graph is child


def test_same_nested_graph_definition_may_be_reused_by_sibling_nodes() -> None:
    child = single_node_graph("child.graph", 1, "child")
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("second", child), nested("first", child)),
        edges=(),
        entries=(NodeId("second"), NodeId("first")),
    )

    compiled = compile_graph(parent)

    assert tuple(compiled.nodes) == (NodeId("first"), NodeId("second"))
    assert compiled.nodes[NodeId("first")].graph is child  # type: ignore[union-attr]
    assert compiled.nodes[NodeId("second")].graph is child  # type: ignore[union-attr]


def test_distinct_nested_graphs_cannot_share_identity_and_version() -> None:
    first_child = single_node_graph("child.graph", 1, "first_step")
    second_child = single_node_graph("child.graph", 1, "second_step")
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("first", first_child), nested("second", second_child)),
        edges=(),
        entries=(NodeId("first"), NodeId("second")),
    )

    with pytest.raises(DuplicateGraphDefinitionError):
        compile_graph(parent)


def test_nested_graphs_may_share_definition_id_across_versions() -> None:
    first_child = single_node_graph("child.graph", 1)
    second_child = single_node_graph("child.graph", 2)
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("first", first_child), nested("second", second_child)),
        edges=(),
        entries=(NodeId("first"), NodeId("second")),
    )

    assert tuple(compile_graph(parent).nodes) == (NodeId("first"), NodeId("second"))


def test_direct_recursive_nested_graph_fails_with_typed_error() -> None:
    root = single_node_graph("root.graph", 1, "leaf")
    object.__setattr__(root, "nodes", (nested("self", root),))
    object.__setattr__(root, "entries", (NodeId("self"),))

    with pytest.raises(RecursiveGraphDefinitionError):
        compile_graph(root)


def test_indirect_recursive_nested_graph_fails_with_typed_error() -> None:
    root = single_node_graph("root.graph", 1, "root_leaf")
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("root", root),),
        edges=(),
        entries=(NodeId("root"),),
    )
    object.__setattr__(root, "nodes", (nested("child", child),))
    object.__setattr__(root, "entries", (NodeId("child"),))

    with pytest.raises(RecursiveGraphDefinitionError):
        compile_graph(root)

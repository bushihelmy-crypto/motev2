from dataclasses import replace

import pytest
from tests.execution.graph.factories import node

from mote_kernel.execution import Graph
from mote_kernel.execution.errors import (
    DuplicateEdgeError,
    DuplicateGraphDefinitionError,
    GraphValidationError,
    MissingEntryError,
    RecursiveGraphDefinitionError,
    UnknownNodeError,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
)
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId, GraphRouteId


def nested(node_id: str, graph: GraphDefinition[str]) -> NestedGraphNodeDefinition[str]:
    return NestedGraphNodeDefinition(
        GraphNodeId(node_id),
        graph,
        normalize_input_bindings({"value": Graph.graph_input("value", str)}),
    )


def single_node_graph(definition_id: str, version: int, node_id: str = "step") -> GraphDefinition[str]:
    return GraphDefinition(
        definition_id=GraphDefinitionId(definition_id),
        version=GraphDefinitionVersion(version),
        nodes=(node(node_id),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )


def test_nested_graph_definition_is_preserved_and_validated() -> None:
    child = single_node_graph("child.graph", 2, "child")
    child_node = nested("nested", child)
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(child_node,),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )

    compiled = compile_graph(parent)

    assert compiled.nodes[GraphNodeId("nested")] is child_node
    assert child_node.graph.definition_id == GraphDefinitionId("child.graph")
    assert child_node.graph.version == GraphDefinitionVersion(2)


def test_invalid_nested_graph_fails_parent_compilation() -> None:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(node("child"),),
        edges=(DirectEdge(GraphNodeId("child"), GraphNodeId("child")),),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("nested", child),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )

    with pytest.raises(MissingEntryError):
        compile_graph(parent)


def test_invalid_deeply_nested_graph_fails_root_compilation() -> None:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(node("child"),),
        edges=(DirectEdge(GraphNodeId("child"), GraphNodeId("child")),),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    middle = GraphDefinition(
        definition_id=GraphDefinitionId("middle.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("child", child),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    root = GraphDefinition(
        definition_id=GraphDefinitionId("root.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("middle", middle),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )

    with pytest.raises(MissingEntryError):
        compile_graph(root)


def test_nested_compilation_preserves_definition_order_error_priority() -> None:
    unknown_source = replace(
        node("unknown-source"),
        inputs=normalize_input_bindings(
            {
                "value": Graph.graph_input("value", str),
                "broken": Graph.node_output("unknown", "value"),
            }
        ),
    )
    self_source = replace(
        node("self-source"),
        inputs=normalize_input_bindings(
            {
                "value": Graph.graph_input("value", str),
                "broken": Graph.node_output("self-source", "value"),
            }
        ),
    )
    first_child = GraphDefinition(
        definition_id=GraphDefinitionId("first-child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(unknown_source,),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    second_child = GraphDefinition(
        definition_id=GraphDefinitionId("second-child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(self_source,),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )

    first_parent = GraphDefinition(
        definition_id=GraphDefinitionId("first-parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("first", first_child), nested("second", second_child)),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    with pytest.raises(UnknownNodeError, match="value source references unknown node"):
        compile_graph(first_parent)

    second_parent = GraphDefinition(
        definition_id=GraphDefinitionId("second-parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("second", second_child), nested("first", first_child)),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    with pytest.raises(GraphValidationError, match="cannot bind its own output"):
        compile_graph(second_parent)


def test_nested_graph_with_duplicate_route_fails_parent_compilation() -> None:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(node("a"), node("b"), node("c")),
        edges=(
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("next"), GraphNodeId("b")),
            ConditionalEdge(GraphNodeId("a"), GraphRouteId("next"), GraphNodeId("c")),
        ),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("nested", child),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
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
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    root = GraphDefinition(
        definition_id=GraphDefinitionId("root.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("step", middle),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )

    root_node = compile_graph(root).nodes[GraphNodeId("step")]

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
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )

    compiled = compile_graph(parent)

    assert tuple(compiled.nodes) == (GraphNodeId("first"), GraphNodeId("second"))
    first = compiled.nodes[GraphNodeId("first")]
    second = compiled.nodes[GraphNodeId("second")]
    assert isinstance(first, NestedGraphNodeDefinition)
    assert isinstance(second, NestedGraphNodeDefinition)
    assert first.graph is child
    assert second.graph is child


def test_distinct_nested_graphs_cannot_share_identity_and_version() -> None:
    first_child = single_node_graph("child.graph", 1, "first_step")
    second_child = single_node_graph("child.graph", 1, "second_step")
    parent = GraphDefinition(
        definition_id=GraphDefinitionId("parent.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("first", first_child), nested("second", second_child)),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
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
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )

    assert tuple(compile_graph(parent).nodes) == (GraphNodeId("first"), GraphNodeId("second"))


def test_direct_recursive_nested_graph_fails_with_typed_error() -> None:
    root = single_node_graph("root.graph", 1, "leaf")
    object.__setattr__(root, "nodes", (nested("self", root),))
    object.__setattr__(root, "entries", ())

    with pytest.raises(RecursiveGraphDefinitionError):
        compile_graph(root)


def test_indirect_recursive_nested_graph_fails_with_typed_error() -> None:
    root = single_node_graph("root.graph", 1, "root_leaf")
    child = GraphDefinition(
        definition_id=GraphDefinitionId("child.graph"),
        version=GraphDefinitionVersion(1),
        nodes=(nested("root", root),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    object.__setattr__(root, "nodes", (nested("child", child),))
    object.__setattr__(root, "entries", ())

    with pytest.raises(RecursiveGraphDefinitionError):
        compile_graph(root)

from mote_kernel.execution.graph import (
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    JoinEdge,
    NodeDefinition,
    NodeSuccess,
)
from mote_kernel.execution.resource import ResourceDefinition


async def identity(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


def node(node_id: str) -> NodeDefinition[str, str]:
    return NodeDefinition(GraphNodeId(node_id), identity)


def graph(
    *,
    nodes: tuple[NodeDefinition[str, str], ...],
    edges: tuple[DirectEdge | ConditionalEdge | JoinEdge, ...] = (),
    entries: tuple[GraphNodeId, ...] = (GraphNodeId("a"),),
    resources: tuple[ResourceDefinition, ...] = (),
) -> GraphDefinition[str, str]:
    return GraphDefinition(
        definition_id=GraphDefinitionId("test.graph"),
        version=GraphDefinitionVersion(1),
        nodes=nodes,
        edges=edges,
        entries=entries,
        resources=resources,
    )

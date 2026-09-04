from mote_kernel.execution import Graph
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    GraphOutputDeclarations,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.topology import CompiledJoin
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphJoinIdentity, GraphNodeId


async def identity(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


def node(node_id: str) -> CallableNodeDefinition[str]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        identity,
        normalize_input_bindings({"value": Graph.graph_input("value", str)}),
        normalize_output_declarations({"value": str}),
    )


def compiled_join(
    sources: tuple[str, ...],
    target: str,
    offsets: tuple[int, ...] | None = None,
) -> CompiledJoin:
    canonical_sources = tuple(sorted(GraphNodeId(source) for source in sources))
    source_offsets = offsets if offsets is not None else (1,) * len(canonical_sources)
    return CompiledJoin(
        GraphJoinIdentity(canonical_sources, GraphNodeId(target)),
        tuple(zip(canonical_sources, source_offsets, strict=True)),
    )


def graph(
    *,
    nodes: tuple[CallableNodeDefinition[str], ...],
    edges: tuple[DirectEdge | ConditionalEdge | JoinEdge, ...] = (),
    entries: tuple[GraphNodeId, ...] = (),
    resources: tuple[ResourceDefinition, ...] = (),
    outputs: GraphOutputDeclarations[str] | None = None,
) -> GraphDefinition[str]:
    return GraphDefinition(
        definition_id=GraphDefinitionId("test.graph"),
        version=GraphDefinitionVersion(1),
        nodes=nodes,
        edges=edges,
        entries=entries,
        outputs=normalize_graph_output_declarations({}) if outputs is None else outputs,
        resources=resources,
    )

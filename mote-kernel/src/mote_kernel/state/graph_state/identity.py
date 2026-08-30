"""Stable scalar identities and coordinate projections for graph state."""

from typing import NewType

GraphRunId = NewType("GraphRunId", str)
GraphDefinitionId = NewType("GraphDefinitionId", str)
GraphDefinitionVersion = NewType("GraphDefinitionVersion", int)
GraphNodeId = NewType("GraphNodeId", str)
GraphRouteId = NewType("GraphRouteId", str)
GraphExecutionAttemptId = NewType("GraphExecutionAttemptId", str)
GraphInterruptId = NewType("GraphInterruptId", str)


def is_canonical_identity(value: str) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and "\n" not in value and "\r" not in value


def _identity_field(value: str) -> str:
    return f"{len(value)}:{value}"


def graph_interrupt_id(
    run_id: GraphRunId,
    superstep: int,
    node_id: GraphNodeId,
    execution_generation: int,
) -> GraphInterruptId:
    """Project one node-interrupt coordinate into its stable comparison ID."""

    values = (
        "mote.graph-node-interrupt.v1",
        str(run_id),
        str(superstep),
        str(node_id),
        str(execution_generation),
    )
    return GraphInterruptId("".join(_identity_field(value) for value in values))


def child_graph_run_id(
    parent_run_id: GraphRunId,
    parent_superstep: int,
    parent_node_id: GraphNodeId,
) -> GraphRunId:
    """Project one parent activation into its deterministic child run ID."""

    values = (
        "mote.child-graph-run.v1",
        str(parent_run_id),
        str(parent_superstep),
        str(parent_node_id),
    )
    return GraphRunId("".join(_identity_field(value) for value in values))


__all__ = [
    "GraphDefinitionId",
    "GraphDefinitionVersion",
    "GraphExecutionAttemptId",
    "GraphInterruptId",
    "GraphNodeId",
    "GraphRouteId",
    "GraphRunId",
    "child_graph_run_id",
    "graph_interrupt_id",
]

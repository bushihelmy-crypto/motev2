"""Stable scalar identities and activation coordinates owned by graph state."""

from dataclasses import dataclass
from typing import NewType

GraphRunId = NewType("GraphRunId", str)
GraphDefinitionId = NewType("GraphDefinitionId", str)
GraphDefinitionVersion = NewType("GraphDefinitionVersion", int)
GraphNodeId = NewType("GraphNodeId", str)
GraphRouteId = NewType("GraphRouteId", str)
GraphExecutionAttemptId = NewType("GraphExecutionAttemptId", str)
GraphInterruptId = NewType("GraphInterruptId", str)


@dataclass(frozen=True, slots=True, order=True)
class GraphActivationIdentity:
    """The durable identity of one node activation in one graph run."""

    run_id: GraphRunId
    superstep: int
    node_id: GraphNodeId

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.run_id):
            raise ValueError("graph activation run_id must be canonical")
        if type(self.superstep) is not int or self.superstep < 0:
            raise ValueError("graph activation superstep must be a non-negative integer")
        if not is_canonical_identity(self.node_id):
            raise ValueError("graph activation node_id must be canonical")


@dataclass(frozen=True, slots=True, order=True)
class ActivationReference:
    """A durable reference to a settled source activation and its route."""

    activation: GraphActivationIdentity
    route: GraphRouteId | None = None

    def __post_init__(self) -> None:
        if type(self.activation) is not GraphActivationIdentity:
            raise ValueError("activation reference requires GraphActivationIdentity")
        if self.route is not None and not is_canonical_identity(self.route):
            raise ValueError("activation reference route must be canonical")

    def canonical_key(self) -> tuple[GraphRunId, int, GraphNodeId, bool, str]:
        """Return the one ordering used by every durable reference collection."""

        return (
            self.activation.run_id,
            self.activation.superstep,
            self.activation.node_id,
            self.route is not None,
            self.route or "",
        )


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
    "ActivationReference",
    "GraphActivationIdentity",
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

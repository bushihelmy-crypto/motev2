"""Authoritative settlement model for one graph frontier."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType, TypeAlias

from mote_kernel.state.graph_state.identity import GraphNodeId, GraphRunId
from mote_kernel.state.graph_state.routing import GraphRoutingContribution

GraphFailure = NewType("GraphFailure", str)
GraphInterruptPayload = NewType("GraphInterruptPayload", bytes)
GraphResumeInputPayload = NewType("GraphResumeInputPayload", bytes)
GraphResumeInputCodecId = NewType("GraphResumeInputCodecId", str)
GraphSkipReason = NewType("GraphSkipReason", str)


class GraphFrontierStatus(Enum):
    """Derived execution disposition of a complete frontier."""

    EXECUTABLE = auto()
    AWAITING_RESUME = auto()
    SETTLED = auto()


@dataclass(frozen=True, slots=True)
class GraphResumeInputCodec:
    codec_id: GraphResumeInputCodecId
    version: int


@dataclass(frozen=True, slots=True)
class UseStepRequestInput:
    """Use the ordinary input carried by the next step request."""


@dataclass(frozen=True, slots=True)
class OverrideGraphNodeInput:
    payload: GraphResumeInputPayload


GraphNodeInputBinding: TypeAlias = UseStepRequestInput | OverrideGraphNodeInput


@dataclass(frozen=True, slots=True)
class PendingGraphNode:
    input: GraphNodeInputBinding


@dataclass(frozen=True, slots=True)
class SucceededGraphNode:
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class FailedGraphNode:
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class GraphNodeInterruptIdentity:
    run_id: GraphRunId
    superstep: int
    node_id: GraphNodeId
    execution_generation: int


@dataclass(frozen=True, slots=True)
class GraphNodeInterrupt:
    identity: GraphNodeInterruptIdentity
    request_payload: GraphInterruptPayload


@dataclass(frozen=True, slots=True)
class InterruptedGraphNode:
    interrupt: GraphNodeInterrupt


@dataclass(frozen=True, slots=True)
class SkippedGraphNode:
    failure: GraphFailure
    reason: GraphSkipReason
    routing: GraphRoutingContribution


GraphNodeSettlement: TypeAlias = (
    PendingGraphNode | SucceededGraphNode | FailedGraphNode | InterruptedGraphNode | SkippedGraphNode
)


@dataclass(frozen=True, slots=True)
class GraphFrontierNode:
    node_id: GraphNodeId
    settlement: GraphNodeSettlement


@dataclass(frozen=True, slots=True)
class GraphFrontierState:
    nodes: tuple[GraphFrontierNode, ...]


def frontier_status(frontier: GraphFrontierState) -> GraphFrontierStatus:
    """Derive executable, awaiting-resume, or settled state in fixed priority order."""

    settlements = tuple(node.settlement for node in frontier.nodes)
    if any(isinstance(settlement, PendingGraphNode) for settlement in settlements):
        return GraphFrontierStatus.EXECUTABLE
    if any(isinstance(settlement, FailedGraphNode | InterruptedGraphNode) for settlement in settlements):
        return GraphFrontierStatus.AWAITING_RESUME
    if settlements and all(isinstance(settlement, SucceededGraphNode | SkippedGraphNode) for settlement in settlements):
        return GraphFrontierStatus.SETTLED
    raise ValueError("a graph frontier has no valid derived status")


def frontier_node(frontier: GraphFrontierState, node_id: GraphNodeId) -> GraphFrontierNode | None:
    return next((node for node in frontier.nodes if node.node_id == node_id), None)


def _node_ids(frontier: GraphFrontierState, settlement_type: type[GraphNodeSettlement]) -> tuple[GraphNodeId, ...]:
    return tuple(node.node_id for node in frontier.nodes if isinstance(node.settlement, settlement_type))


def pending_node_ids(frontier: GraphFrontierState) -> tuple[GraphNodeId, ...]:
    return _node_ids(frontier, PendingGraphNode)


def failed_node_ids(frontier: GraphFrontierState) -> tuple[GraphNodeId, ...]:
    return _node_ids(frontier, FailedGraphNode)


def interrupted_node_ids(frontier: GraphFrontierState) -> tuple[GraphNodeId, ...]:
    return _node_ids(frontier, InterruptedGraphNode)


def routing_contributions(
    frontier: GraphFrontierState,
) -> tuple[tuple[GraphNodeId, GraphRoutingContribution], ...]:
    return tuple(
        (node.node_id, node.settlement.routing)
        for node in frontier.nodes
        if isinstance(node.settlement, SucceededGraphNode | SkippedGraphNode)
    )


__all__ = [
    "FailedGraphNode",
    "GraphFailure",
    "GraphFrontierNode",
    "GraphFrontierState",
    "GraphFrontierStatus",
    "GraphInterruptPayload",
    "GraphNodeInputBinding",
    "GraphNodeInterrupt",
    "GraphNodeInterruptIdentity",
    "GraphNodeSettlement",
    "GraphResumeInputCodec",
    "GraphResumeInputCodecId",
    "GraphResumeInputPayload",
    "GraphSkipReason",
    "InterruptedGraphNode",
    "OverrideGraphNodeInput",
    "PendingGraphNode",
    "SkippedGraphNode",
    "SucceededGraphNode",
    "UseStepRequestInput",
    "failed_node_ids",
    "frontier_node",
    "frontier_status",
    "interrupted_node_ids",
    "pending_node_ids",
    "routing_contributions",
]

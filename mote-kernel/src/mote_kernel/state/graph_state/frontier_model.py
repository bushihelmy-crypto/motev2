"""Authoritative activation and settlement model for one graph frontier."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import NewType, TypeAlias

from mote_kernel.state.graph_state.identity import (
    ActivationReference,
    GraphJoinOccurrenceIdentity,
    GraphNodeId,
    GraphRunId,
    is_canonical_identity,
)
from mote_kernel.state.graph_state.routing import GraphRoutingContribution

GraphFailure = NewType("GraphFailure", str)
GraphInterruptPayload = NewType("GraphInterruptPayload", bytes)
GraphResumeInputPayload = NewType("GraphResumeInputPayload", bytes)
GraphResumeInputCodecId = NewType("GraphResumeInputCodecId", str)


@dataclass(frozen=True, slots=True)
class StartActivationCause:
    """The cause of an activation admitted directly from graph START."""


@dataclass(frozen=True, slots=True)
class RoutedActivationCause:
    """The settled routing facts that admitted a new activation."""

    references: tuple[ActivationReference, ...]
    join_occurrence: GraphJoinOccurrenceIdentity | None = None

    def __post_init__(self) -> None:
        if type(self.references) is not tuple or not self.references:
            raise ValueError("routed activation cause requires at least one reference")
        if any(type(reference) is not ActivationReference for reference in self.references):
            raise ValueError("routed activation cause references must be typed values")
        canonical = tuple(
            sorted(
                set(self.references),
                key=ActivationReference.canonical_key,
            )
        )
        if self.references != canonical:
            raise ValueError("routed activation cause references must be canonical and distinct")
        occurrence = self.join_occurrence
        if occurrence is None:
            if len(self.references) != 1:
                raise ValueError("non-Join routed cause requires exactly one reference")
            return
        if type(occurrence) is not GraphJoinOccurrenceIdentity:
            raise ValueError("routed Join cause requires GraphJoinOccurrenceIdentity")
        source_ids = tuple(reference.activation.node_id for reference in self.references)
        if len(source_ids) != len(set(source_ids)) or set(source_ids) != set(occurrence.join.sources):
            raise ValueError("routed Join cause must contain exactly one reference per source")
        if any(
            reference.activation.run_id != occurrence.run_id
            or reference.activation.superstep >= occurrence.target_superstep
            for reference in self.references
        ):
            raise ValueError("routed Join cause references must precede their target coordinate")


GraphActivationCause: TypeAlias = StartActivationCause | RoutedActivationCause


@dataclass(frozen=True, slots=True)
class GraphFrontierActivation:
    """A frontier node identity together with its durable activation cause."""

    node_id: GraphNodeId
    cause: GraphActivationCause

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.node_id):
            raise ValueError("frontier activation node_id must be canonical")
        if type(self.cause) not in (StartActivationCause, RoutedActivationCause):
            raise ValueError("frontier activation cause has an unsupported variant")


class GraphFrontierStatus(Enum):
    """Derived execution disposition of one durable frontier."""

    EXECUTABLE = auto()
    FAILED = auto()
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


GraphNodeSettlement: TypeAlias = PendingGraphNode | SucceededGraphNode | FailedGraphNode | InterruptedGraphNode


@dataclass(frozen=True, slots=True)
class GraphFrontierNode:
    node_id: GraphNodeId
    settlement: GraphNodeSettlement
    cause: GraphActivationCause

    @property
    def activation(self) -> GraphFrontierActivation:
        """Return the state-owned activation record for this frontier node."""

        return GraphFrontierActivation(self.node_id, self.cause)


@dataclass(frozen=True, slots=True)
class GraphFrontierState:
    nodes: tuple[GraphFrontierNode, ...]


def frontier_status(frontier: GraphFrontierState) -> GraphFrontierStatus:
    """Derive frontier disposition in the kernel's fixed priority order."""

    settlements = tuple(node.settlement for node in frontier.nodes)
    if any(isinstance(settlement, PendingGraphNode) for settlement in settlements):
        return GraphFrontierStatus.EXECUTABLE
    if any(isinstance(settlement, FailedGraphNode) for settlement in settlements):
        return GraphFrontierStatus.FAILED
    if any(isinstance(settlement, InterruptedGraphNode) for settlement in settlements):
        return GraphFrontierStatus.AWAITING_RESUME
    if settlements and all(isinstance(settlement, SucceededGraphNode) for settlement in settlements):
        return GraphFrontierStatus.SETTLED
    raise ValueError("a graph frontier has no valid derived status")


def frontier_node(frontier: GraphFrontierState, node_id: GraphNodeId) -> GraphFrontierNode | None:
    return next((node for node in frontier.nodes if node.node_id == node_id), None)


def _node_ids(frontier: GraphFrontierState, settlement_type: type[GraphNodeSettlement]) -> tuple[GraphNodeId, ...]:
    return tuple(node.node_id for node in frontier.nodes if isinstance(node.settlement, settlement_type))


def pending_node_ids(frontier: GraphFrontierState) -> tuple[GraphNodeId, ...]:
    return _node_ids(frontier, PendingGraphNode)


def interrupted_node_ids(frontier: GraphFrontierState) -> tuple[GraphNodeId, ...]:
    return _node_ids(frontier, InterruptedGraphNode)


def routing_contributions(
    frontier: GraphFrontierState,
) -> tuple[tuple[GraphNodeId, GraphRoutingContribution], ...]:
    return tuple(
        (node.node_id, node.settlement.routing)
        for node in frontier.nodes
        if isinstance(node.settlement, SucceededGraphNode)
    )


__all__ = [
    "FailedGraphNode",
    "GraphActivationCause",
    "GraphFailure",
    "GraphFrontierActivation",
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
    "InterruptedGraphNode",
    "OverrideGraphNodeInput",
    "PendingGraphNode",
    "RoutedActivationCause",
    "StartActivationCause",
    "SucceededGraphNode",
    "UseStepRequestInput",
    "frontier_node",
    "frontier_status",
    "interrupted_node_ids",
    "pending_node_ids",
    "routing_contributions",
]

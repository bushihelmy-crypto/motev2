"""Deterministic identities for durable graph events."""

from typing import Final, NewType

from mote_kernel.state.graph_state import GraphNodeId, GraphRunId

NodeSettlementEventId = NewType("NodeSettlementEventId", str)
NODE_SETTLEMENT_EVENT_SCHEMA_VERSION: Final[int] = 1


def node_settlement_event_id(
    run_id: GraphRunId,
    scope: tuple[str, ...],
    superstep: int,
    node_id: GraphNodeId,
    execution_generation: int,
    settlement_revision: int,
) -> NodeSettlementEventId:
    """Project one settlement coordinate into a stable idempotency key."""

    values = (
        f"mote.node-settlement-event.v{NODE_SETTLEMENT_EVENT_SCHEMA_VERSION}",
        str(run_id),
        str(len(scope)),
        *scope,
        str(superstep),
        str(node_id),
        str(execution_generation),
        str(settlement_revision),
    )
    return NodeSettlementEventId("".join(f"{len(value)}:{value}" for value in values))

"""Immutable outbox references projected from graph settlement snapshots."""

from dataclasses import dataclass
from typing import ClassVar

from mote_kernel.events.identity import (
    NODE_SETTLEMENT_EVENT_SCHEMA_VERSION,
    NodeSettlementEventId,
    node_settlement_event_id,
)
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId
from mote_kernel.state.graph_state.identity import is_canonical_identity


@dataclass(frozen=True, slots=True)
class NodeSettlementEventReference:
    """A durable pointer to one node settlement in one state revision."""

    # The schema is fixed for this reference type; a new schema gets a new identity prefix.
    schema_version: ClassVar[int] = NODE_SETTLEMENT_EVENT_SCHEMA_VERSION

    run_id: GraphRunId
    scope: tuple[str, ...]
    superstep: int
    node_id: GraphNodeId
    execution_generation: int
    settlement_revision: int

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.run_id) or not is_canonical_identity(self.node_id):
            raise ValueError("event reference identities must be canonical")
        if type(self.scope) is not tuple or any(not is_canonical_identity(part) for part in self.scope):
            raise ValueError("event reference scope must be an immutable tuple of canonical identities")
        if type(self.superstep) is not int or self.superstep < 0:
            raise ValueError("event reference superstep must be a non-negative integer")
        if type(self.execution_generation) is not int or self.execution_generation < 1:
            raise ValueError("event reference execution generation must be a positive integer")
        if type(self.settlement_revision) is not int or self.settlement_revision < 0:
            raise ValueError("event reference settlement revision must be a non-negative integer")

    @property
    def event_id(self) -> NodeSettlementEventId:
        """Return the deterministic delivery identity for this reference."""

        return node_settlement_event_id(
            self.run_id,
            self.scope,
            self.superstep,
            self.node_id,
            self.execution_generation,
            self.settlement_revision,
        )

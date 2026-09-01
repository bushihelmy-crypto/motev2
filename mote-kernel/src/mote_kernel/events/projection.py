"""Pure projection from graph transitions to durable event references."""

from typing import TypeVar

from mote_kernel.events.record import NodeSettlementEventReference
from mote_kernel.execution import Graph
from mote_kernel.state.graph_state import SettleGraphNode

GraphValueT = TypeVar("GraphValueT")


def project_event(
    transition: Graph.Transition[GraphValueT],
    /,
) -> NodeSettlementEventReference | None:
    """Reference one node settlement; other transitions add no outbox row."""

    command = transition.command
    if not isinstance(command, SettleGraphNode):
        return None
    candidate = transition.candidate_state
    return NodeSettlementEventReference(
        run_id=candidate.run_id,
        scope=transition.scope,
        superstep=candidate.superstep,
        node_id=command.outcome.node_id,
        execution_generation=command.execution.generation,
        settlement_revision=candidate.revision,
    )

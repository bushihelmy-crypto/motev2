"""Preparation and verification of frontier-wide execution claims."""

from typing import TypeVar
from uuid import uuid4

from mote_kernel.execution.claim import ExecutionClaimOwner, ExecutionClaimSnapshot, PreparedExecutionClaim
from mote_kernel.execution.engine.frontier import FrontierPreparation
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphRunState,
    ResourceSnapshot,
)

GraphValueT = TypeVar("GraphValueT")


def prepare_claim(
    owner: ExecutionClaimOwner,
    preparation: FrontierPreparation[GraphValueT],
    resources: ResourceSnapshot | None,
) -> PreparedExecutionClaim[GraphValueT]:
    state = preparation.request.state
    attempt_id = GraphExecutionAttemptId(str(uuid4()))
    token = GraphExecutionToken(state.execution_sequence + 1, attempt_id)
    command = project_claim_command(state, attempt_id, resources)
    return PreparedExecutionClaim(
        owner,
        ExecutionClaimSnapshot(command, token),
        preparation,
    )


def project_claim_command(
    state: GraphRunState,
    attempt_id: GraphExecutionAttemptId,
    resources: ResourceSnapshot | None,
) -> ClaimGraphExecution:
    return ClaimGraphExecution(state.revision, attempt_id, resources)


__all__ = ["prepare_claim", "project_claim_command"]

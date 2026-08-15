"""Preparation and verification of frontier-wide execution claims."""

from uuid import uuid4

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim, prepare_execution_claim
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.request import ExecutionRequestAttemptId
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphRunState,
    ResourceSnapshot,
)


def prepare_claim(
    owner: ExecutionClaimOwner,
    state: GraphRunState,
    request_attempt_id: ExecutionRequestAttemptId,
    tasks: tuple[GraphTask, ...],
    resources: ResourceSnapshot | None,
) -> PreparedExecutionClaim:
    node_ids = tuple(task.node_id for task in tasks)
    task_ids = tuple(task.task_id for task in tasks)
    attempt_id = GraphExecutionAttemptId(str(uuid4()))
    token = GraphExecutionToken(state.execution_sequence + 1, attempt_id)
    command = ClaimGraphExecution(state.revision, attempt_id, resources)
    return prepare_execution_claim(owner, command, token, node_ids, task_ids, request_attempt_id)


def require_claim_tasks(claim: PreparedExecutionClaim, tasks: tuple[GraphTask, ...]) -> None:
    if claim.snapshot.node_ids != tuple(task.node_id for task in tasks) or claim.snapshot.task_ids != tuple(
        task.task_id for task in tasks
    ):
        raise ResultCollectionError("execution claim tasks do not match current pending nodes")


__all__ = ["prepare_claim", "require_claim_tasks"]

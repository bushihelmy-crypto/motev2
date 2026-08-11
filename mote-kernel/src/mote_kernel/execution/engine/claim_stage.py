"""Preparation and verification of uniquely identified execution claims."""

from uuid import uuid4

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim, prepare_execution_claim
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.snapshot import ExecutionAttemptId, ExecutionTaskId, ExecutionToken
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    GraphExecutionAttemptId,
    GraphRunState,
    GraphTaskId,
)


def interrupt_generation(state: GraphRunState) -> int | None:
    """Return the exact interrupt generation observed by a prepared command."""

    interrupt = state.interrupt
    return interrupt.identity.generation if interrupt is not None else None


def prepare_claim(
    owner: ExecutionClaimOwner,
    state: GraphRunState,
    attempt_id: ExecutionAttemptId,
    tasks: tuple[GraphTask, ...],
) -> PreparedExecutionClaim:
    """Prepare one linear capability and its authoritative claim command."""

    task_ids = tuple(sorted(ExecutionTaskId(task.task_id) for task in tasks))
    claim_id = ExecutionAttemptId(str(uuid4()))
    token = ExecutionToken(state.execution_sequence + 1, claim_id)
    command = ClaimGraphExecution(
        state.superstep,
        state.execution_sequence,
        state.parallel,
        interrupt_generation(state),
        GraphExecutionAttemptId(claim_id),
        tuple(GraphTaskId(task_id) for task_id in task_ids),
    )
    return prepare_execution_claim(owner, command, token, task_ids, attempt_id)


def require_claim_tasks(claim: PreparedExecutionClaim, tasks: tuple[GraphTask, ...]) -> None:
    """Reject any recomputed task batch that differs from the accepted claim."""

    task_ids = tuple(sorted(ExecutionTaskId(task.task_id) for task in tasks))
    if claim.snapshot.task_ids != task_ids:
        raise ResultCollectionError("execution claim tasks do not match the prepared frontier")


__all__ = ["interrupt_generation", "prepare_claim", "require_claim_tasks"]

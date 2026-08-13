"""Linear execution claims owned by one assembled graph executor."""

import asyncio
from dataclasses import dataclass

from mote_kernel.execution.engine.task import TaskId
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    GraphExecutionToken,
    GraphNodeId,
    GraphRunState,
)


class ExecutionClaimOwner:
    """Nominal identity proving which assembled executor owns a linear claim."""


@dataclass(frozen=True, slots=True)
class ExecutionClaimSnapshot:
    command: ClaimGraphExecution
    token: GraphExecutionToken
    node_ids: tuple[GraphNodeId, ...]
    task_ids: tuple[TaskId, ...]
    request_attempt_id: ExecutionRequestAttemptId


class PreparedExecutionClaim:
    __slots__ = ("_consumed", "_gate", "_owner", "snapshot")

    def __init__(self, owner: ExecutionClaimOwner, snapshot: ExecutionClaimSnapshot) -> None:
        self.snapshot = snapshot
        self._owner = owner
        self._gate = asyncio.Lock()
        self._consumed = False

    @property
    def command(self) -> ClaimGraphExecution:
        return self.snapshot.command

    @property
    def consumed(self) -> bool:
        return self._consumed

    async def consume(
        self,
        owner: ExecutionClaimOwner,
        state: GraphRunState,
        request_attempt_id: ExecutionRequestAttemptId,
    ) -> None:
        async with self._gate:
            if self._consumed:
                raise ResultCollectionError("execution claim has already been consumed")
            execution = state.execution
            snapshot = self.snapshot
            if (
                owner is not self._owner
                or execution is None
                or execution.token != snapshot.token
                or execution.node_ids != snapshot.node_ids
                or request_attempt_id != snapshot.request_attempt_id
            ):
                raise ResultCollectionError("execution claim does not match committed graph state")
            self._consumed = True


def prepare_execution_claim(
    owner: ExecutionClaimOwner,
    command: ClaimGraphExecution,
    token: GraphExecutionToken,
    node_ids: tuple[GraphNodeId, ...],
    task_ids: tuple[TaskId, ...],
    request_attempt_id: ExecutionRequestAttemptId,
) -> PreparedExecutionClaim:
    return PreparedExecutionClaim(
        owner,
        ExecutionClaimSnapshot(command, token, node_ids, task_ids, request_attempt_id),
    )


__all__ = ["ExecutionClaimOwner", "ExecutionClaimSnapshot", "PreparedExecutionClaim"]

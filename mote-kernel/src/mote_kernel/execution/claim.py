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


class _ClaimConsumptionAuthority:
    __slots__ = ()


_CLAIM_CONSUMPTION_AUTHORITY = _ClaimConsumptionAuthority()


class ExecutionClaimOwner:
    """Nominal identity proving which assembled executor owns a linear claim."""


@dataclass(frozen=True, slots=True)
class ExecutionClaimSnapshot:
    command: ClaimGraphExecution
    token: GraphExecutionToken
    node_ids: tuple[GraphNodeId, ...]
    task_ids: tuple[TaskId, ...]
    request_attempt_id: ExecutionRequestAttemptId


class ConsumedExecutionClaim:
    """Internal one-shot receipt authorizing one session construction."""

    __slots__ = ("_issued", "_snapshot")

    def __init__(
        self,
        authority: _ClaimConsumptionAuthority,
        snapshot: ExecutionClaimSnapshot,
    ) -> None:
        if authority is not _CLAIM_CONSUMPTION_AUTHORITY:
            raise TypeError("consumed execution claims are issued only by PreparedExecutionClaim.consume()")
        self._snapshot = snapshot
        self._issued = False

    def issue(
        self,
        state: GraphRunState,
        request_attempt_id: ExecutionRequestAttemptId,
    ) -> ExecutionClaimSnapshot:
        if self._issued:
            raise ResultCollectionError("consumed execution claim has already issued its session")
        _require_committed_claim_state(self._snapshot, state, request_attempt_id)
        self._issued = True
        return self._snapshot


def _require_committed_claim_state(
    snapshot: ExecutionClaimSnapshot,
    state: GraphRunState,
    request_attempt_id: ExecutionRequestAttemptId,
) -> None:
    execution = state.execution
    if (
        execution is None
        or execution.token != snapshot.token
        or state.revision != snapshot.command.expected_revision + 1
        or state.resources != snapshot.command.resources
        or request_attempt_id != snapshot.request_attempt_id
    ):
        raise ResultCollectionError("execution claim does not match committed graph state")


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
    ) -> ConsumedExecutionClaim:
        async with self._gate:
            if self._consumed:
                raise ResultCollectionError("execution claim has already been consumed")
            snapshot = self.snapshot
            if owner is not self._owner:
                raise ResultCollectionError("execution claim does not match committed graph state")
            _require_committed_claim_state(snapshot, state, request_attempt_id)
            self._consumed = True
            return ConsumedExecutionClaim(_CLAIM_CONSUMPTION_AUTHORITY, snapshot)


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

"""Linear execution claims owned by one assembled graph executor."""

import asyncio
from dataclasses import dataclass

from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.snapshot import ExecutionAttemptId, ExecutionTaskId, ExecutionToken
from mote_kernel.state.graph_state import ClaimGraphExecution, GraphRunState


class ExecutionClaimOwner:
    """Nominal identity proving which assembled executor owns a linear claim."""


@dataclass(frozen=True, slots=True)
class ExecutionClaimSnapshot:
    """Immutable facts prepared for one authoritative claim transition."""

    command: ClaimGraphExecution
    token: ExecutionToken
    task_ids: tuple[ExecutionTaskId, ...]
    source_attempt_id: ExecutionAttemptId


class PreparedExecutionClaim:
    """A one-use capability retained only by the executor that prepared its transition."""

    __slots__ = ("_consumed", "_gate", "_owner", "snapshot")

    def __init__(
        self,
        owner: ExecutionClaimOwner,
        snapshot: ExecutionClaimSnapshot,
    ) -> None:
        self.snapshot = snapshot
        self._owner = owner
        self._gate = asyncio.Lock()
        self._consumed = False

    @property
    def command(self) -> ClaimGraphExecution:
        """Return the command that must be durably accepted before execution."""

        return self.snapshot.command

    @property
    def consumed(self) -> bool:
        """Return whether this linear capability has already started execution."""

        return self._consumed

    async def consume(
        self,
        owner: ExecutionClaimOwner,
        state: GraphRunState,
        attempt_id: ExecutionAttemptId,
    ) -> None:
        """Atomically consume this capability after validating its committed lease."""

        async with self._gate:
            if self._consumed:
                raise ResultCollectionError("execution claim has already been consumed")
            execution = state.execution
            snapshot = self.snapshot
            if owner is not self._owner:
                raise ResultCollectionError("execution claim belongs to another graph executor")
            if (
                execution is None
                or execution.token.generation != snapshot.token.generation
                or execution.token.attempt_id != snapshot.token.attempt_id
                or tuple(ExecutionTaskId(task_id) for task_id in execution.task_ids) != snapshot.task_ids
                or attempt_id != snapshot.source_attempt_id
            ):
                raise ResultCollectionError("execution claim does not match committed graph state")
            self._consumed = True


def prepare_execution_claim(
    owner: ExecutionClaimOwner,
    command: ClaimGraphExecution,
    token: ExecutionToken,
    task_ids: tuple[ExecutionTaskId, ...],
    source_attempt_id: ExecutionAttemptId,
) -> PreparedExecutionClaim:
    """Create one executor-owned linear capability for a prepared claim command."""

    return PreparedExecutionClaim(
        owner,
        ExecutionClaimSnapshot(command, token, task_ids, source_attempt_id),
    )


__all__ = ["ExecutionClaimOwner", "ExecutionClaimSnapshot", "PreparedExecutionClaim"]

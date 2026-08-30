"""Linear execution claims owned by one assembled graph executor."""

import asyncio
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.engine.frontier import FrontierPreparation
from mote_kernel.execution.engine.task import task_identity
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    GraphExecutionToken,
    GraphRunState,
    pending_node_ids,
)

GraphValueT = TypeVar("GraphValueT")


class _ClaimConsumptionAuthority:
    __slots__ = ()


_CLAIM_CONSUMPTION_AUTHORITY = _ClaimConsumptionAuthority()


class ExecutionClaimOwner:
    """Nominal identity proving which assembled executor owns a linear claim."""


@dataclass(frozen=True, slots=True)
class ExecutionClaimSnapshot:
    command: ClaimGraphExecution
    token: GraphExecutionToken


class ConsumedExecutionClaim(Generic[GraphValueT]):
    """Internal one-shot receipt authorizing one session construction."""

    __slots__ = ("_issued", "_preparation", "_snapshot", "_state")

    def __init__(
        self,
        authority: _ClaimConsumptionAuthority,
        snapshot: ExecutionClaimSnapshot,
        state: GraphRunState,
        preparation: FrontierPreparation[GraphValueT],
    ) -> None:
        if authority is not _CLAIM_CONSUMPTION_AUTHORITY:
            raise TypeError("consumed execution claims are issued only by PreparedExecutionClaim.consume()")
        self._snapshot = snapshot
        self._state = state
        self._preparation = preparation
        self._issued = False

    def issue(
        self,
    ) -> tuple[ExecutionClaimSnapshot, GraphRunState, FrontierPreparation[GraphValueT]]:
        if self._issued:
            raise ResultCollectionError("consumed execution claim has already issued its session")
        self._issued = True
        return self._snapshot, self._state, self._preparation


def _require_committed_claim_state(
    snapshot: ExecutionClaimSnapshot,
    state: GraphRunState,
    preparation: FrontierPreparation[GraphValueT],
) -> None:
    execution = state.execution
    node_ids = tuple(task.node_id for task in preparation.tasks)
    if (
        execution is None
        or execution.token != snapshot.token
        or state.revision != snapshot.command.expected_revision + 1
        or state.resources != snapshot.command.resources
        or node_ids != pending_node_ids(state.frontier)
        or tuple(task.task_id for task in preparation.tasks)
        != tuple(task_identity(state.run_id, state.superstep, node_id) for node_id in node_ids)
    ):
        raise ResultCollectionError("execution claim does not match committed graph state")


class PreparedExecutionClaim(Generic[GraphValueT]):
    __slots__ = ("_consumed", "_gate", "_owner", "_preparation", "_snapshot")

    def __init__(
        self,
        owner: ExecutionClaimOwner,
        snapshot: ExecutionClaimSnapshot,
        preparation: FrontierPreparation[GraphValueT],
    ) -> None:
        self._snapshot = snapshot
        self._owner = owner
        self._preparation = preparation
        self._gate = asyncio.Lock()
        self._consumed = False

    @property
    def command(self) -> ClaimGraphExecution:
        return self.snapshot.command

    @property
    def snapshot(self) -> ExecutionClaimSnapshot:
        return self._snapshot

    @property
    def scope_run(self) -> ScopeRunCoordinate:
        return self._preparation.request.scope_run

    @property
    def consumed(self) -> bool:
        return self._consumed

    async def consume(
        self,
        owner: ExecutionClaimOwner,
        state: GraphRunState,
    ) -> ConsumedExecutionClaim[GraphValueT]:
        async with self._gate:
            if self._consumed:
                raise ResultCollectionError("execution claim has already been consumed")
            snapshot = self.snapshot
            if owner is not self._owner:
                raise ResultCollectionError("execution claim does not match committed graph state")
            _require_committed_claim_state(snapshot, state, self._preparation)
            self._consumed = True
            return ConsumedExecutionClaim(_CLAIM_CONSUMPTION_AUTHORITY, snapshot, state, self._preparation)


__all__ = ["ExecutionClaimOwner", "ExecutionClaimSnapshot", "PreparedExecutionClaim"]

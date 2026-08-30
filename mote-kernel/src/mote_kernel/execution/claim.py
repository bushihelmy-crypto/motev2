"""Linear execution claims owned by one assembled graph executor."""

import asyncio
from typing import Generic, TypeVar

from mote_kernel.execution.engine.frontier import FrontierPreparation
from mote_kernel.execution.engine.task import GraphTask, task_identity
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.identity import ScopeRunCoordinate
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    GraphRunState,
    pending_node_ids,
    reduce_graph_run,
)

GraphValueT = TypeVar("GraphValueT")


class _ClaimConsumptionAuthority:
    __slots__ = ()


_CLAIM_CONSUMPTION_AUTHORITY = _ClaimConsumptionAuthority()


class ExecutionClaimOwner:
    """Nominal identity proving which assembled executor owns a linear claim."""


class ConsumedExecutionClaim(Generic[GraphValueT]):
    """Internal one-shot receipt authorizing one session construction."""

    __slots__ = ("_issued", "_preparation", "_state")

    def __init__(
        self,
        authority: _ClaimConsumptionAuthority,
        state: GraphRunState,
        preparation: FrontierPreparation[GraphValueT],
    ) -> None:
        if authority is not _CLAIM_CONSUMPTION_AUTHORITY:
            raise TypeError("consumed execution claims are issued only by PreparedExecutionClaim.consume()")
        self._state = state
        self._preparation = preparation
        self._issued = False

    def issue(self) -> tuple[GraphRunState, FrontierPreparation[GraphValueT]]:
        if self._issued:
            raise ResultCollectionError("consumed execution claim has already issued its session")
        self._issued = True
        return self._state, self._preparation


def _require_committed_claim_state(
    command: ClaimGraphExecution,
    state: GraphRunState,
    preparation: FrontierPreparation[GraphValueT],
) -> None:
    expected_tasks = tuple(
        GraphTask(
            task_identity(state.run_id, state.superstep, node_id),
            state.run_id,
            state.superstep,
            node_id,
        )
        for node_id in pending_node_ids(state.frontier)
    )
    if state != reduce_graph_run(preparation.request.state, command) or preparation.tasks != expected_tasks:
        raise ResultCollectionError("execution claim does not match committed graph state")


class PreparedExecutionClaim(Generic[GraphValueT]):
    __slots__ = ("_command", "_consumed", "_gate", "_owner", "_preparation")

    def __init__(
        self,
        owner: ExecutionClaimOwner,
        command: ClaimGraphExecution,
        preparation: FrontierPreparation[GraphValueT],
    ) -> None:
        self._command = command
        self._owner = owner
        self._preparation = preparation
        self._gate = asyncio.Lock()
        self._consumed = False

    @property
    def command(self) -> ClaimGraphExecution:
        return self._command

    @property
    def scope_run(self) -> ScopeRunCoordinate:
        return self._preparation.request.scope_run

    async def consume(
        self,
        owner: ExecutionClaimOwner,
        state: GraphRunState,
    ) -> ConsumedExecutionClaim[GraphValueT]:
        async with self._gate:
            if self._consumed:
                raise ResultCollectionError("execution claim has already been consumed")
            if owner is not self._owner:
                raise ResultCollectionError("execution claim does not match committed graph state")
            _require_committed_claim_state(self._command, state, self._preparation)
            self._consumed = True
            return ConsumedExecutionClaim(_CLAIM_CONSUMPTION_AUTHORITY, state, self._preparation)


__all__ = ["ExecutionClaimOwner", "PreparedExecutionClaim"]

"""Owner-local graph execution."""

from typing import Generic, TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.session import GraphExecutionSession, issue_execution_session
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import prepare_superstep, validate_execution_session_request
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import PrepareDisposition

GraphValueT = TypeVar("GraphValueT")


class GraphExecutor(Generic[GraphValueT]):
    __slots__ = ("_claim_owner", "_graph")

    def __init__(self, graph: CompiledGraph[GraphValueT]) -> None:
        self._graph = graph
        self._claim_owner = ExecutionClaimOwner()

    async def prepare(self, request: StepRequest[GraphValueT]) -> PrepareDisposition[GraphValueT]:
        require_scoped_snapshot_matches_graph(self._graph, request.state, request.scope_run)
        return await prepare_superstep(self._claim_owner, self._graph, request)

    async def execute(
        self,
        claim: PreparedExecutionClaim,
        request: StepRequest[GraphValueT],
    ) -> GraphExecutionSession[GraphValueT]:
        require_scoped_snapshot_matches_graph(self._graph, request.state, request.scope_run)
        validate_execution_session_request(self._graph, request, claim)
        consumed = await claim.consume(self._claim_owner, request.state, request.request_attempt_id)
        return issue_execution_session(self._graph, request, consumed)


__all__: list[str] = []

"""Owner-local graph execution."""

from typing import Generic, TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.session import GraphExecutionSession, issue_execution_session
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import PrepareDisposition, prepare_superstep
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.request import StepRequest
from mote_kernel.state.graph_state import GraphRunState

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
        claim: PreparedExecutionClaim[GraphValueT],
        state: GraphRunState,
    ) -> GraphExecutionSession[GraphValueT]:
        require_scoped_snapshot_matches_graph(self._graph, state, claim.scope_run)
        consumed = await claim.consume(self._claim_owner, state)
        return issue_execution_session(self._graph, consumed)


__all__: list[str] = []

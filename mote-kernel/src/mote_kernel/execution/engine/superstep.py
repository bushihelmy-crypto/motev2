"""State-driven preparation and session creation for one frontier attempt."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.admission import claim_resource_snapshot
from mote_kernel.execution.engine.claim_stage import prepare_claim
from mote_kernel.execution.engine.frontier import prepare_frontier
from mote_kernel.execution.engine.routing import resolve_routing
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.request import StepRequest
from mote_kernel.execution.result import (
    AbortedGraph,
    AwaitingResume,
    CompletedGraph,
    FailedGraph,
    ReadyToResolve,
    WaitingForChildren,
)
from mote_kernel.state.graph_state import (
    GraphFrontierStatus,
    GraphRunStatus,
    frontier_status,
    interrupted_node_ids,
)

GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class ExecutableFrontier(Generic[GraphValueT]):
    claim: PreparedExecutionClaim[GraphValueT]
    children: WaitingForChildren[GraphValueT] | None


PrepareDisposition: TypeAlias = (
    ExecutableFrontier[GraphValueT]
    | WaitingForChildren[GraphValueT]
    | ReadyToResolve
    | AwaitingResume
    | CompletedGraph
    | FailedGraph
    | AbortedGraph
)


def prepare_superstep(
    owner: ExecutionClaimOwner,
    graph: CompiledGraph[GraphValueT],
    request: StepRequest[GraphValueT],
) -> PrepareDisposition[GraphValueT]:
    require_scoped_snapshot_matches_graph(graph, request.state, request.scope_run)
    state = request.state
    if state.status is GraphRunStatus.COMPLETED:
        return CompletedGraph()
    if state.status is GraphRunStatus.FAILED:
        return FailedGraph()
    if state.status is GraphRunStatus.ABORTED:
        return AbortedGraph()
    status = frontier_status(state.frontier)
    if status is GraphFrontierStatus.SETTLED:
        return ReadyToResolve(resolve_routing(graph, state, request.scope_run, request.frames))
    if status is GraphFrontierStatus.AWAITING_RESUME:
        return AwaitingResume(interrupted_node_ids(state.frontier))
    if state.execution is not None:
        raise ResultCollectionError("active execution requires its original execution session")
    frontier = prepare_frontier(graph, request)
    children: WaitingForChildren[GraphValueT] | None = (
        WaitingForChildren[GraphValueT](frontier.missing_children, frontier.active_children)
        if frontier.missing_children or frontier.active_children
        else None
    )
    if children is not None and not frontier.executables and not frontier.nested_results:
        return children
    claim = prepare_claim(
        owner,
        frontier,
        claim_resource_snapshot(graph, frontier.tasks),
    )
    return ExecutableFrontier(claim, children)


__all__ = ["prepare_superstep"]

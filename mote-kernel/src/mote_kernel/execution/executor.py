"""Sole scoped graph executor with prepare, execute, and resume projections."""

from dataclasses import replace
from typing import Generic, TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.resume_input import (
    decode_resume_input,
    encode_resume_input,
    materialize_node_input,
    require_resume_input_binding,
)
from mote_kernel.execution.engine.routing import validate_routing_contribution
from mote_kernel.execution.engine.session import GraphExecutionSession, issue_execution_session
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import prepare_superstep, validate_execution_session_request
from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
    StepRequest,
)
from mote_kernel.execution.result import PrepareDisposition, PreparedResume
from mote_kernel.execution.run_context import AdmittedResumeInput, ResumeInputAvailabilityCoordinate
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    FailedGraphNode,
    GraphFrontierNode,
    GraphFrontierState,
    GraphNodeId,
    GraphNodeSettlement,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    InterruptedGraphNode,
    PendingGraphNode,
    ResumeFailedNode,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    SelectGraphRoute,
    SkipFailedNode,
    SkippedGraphNode,
    StartGraphRun,
    UseStepRequestInput,
    frontier_node,
    graph_interrupt_id,
)
from mote_kernel.state.graph_state.validation import validate_graph_frontier

GraphValueT = TypeVar("GraphValueT")


class GraphExecutor(Generic[GraphValueT]):
    __slots__ = ("_claim_owner", "_graph")

    def __init__(self, graph: CompiledGraph[GraphValueT]) -> None:
        self._graph = graph
        self._claim_owner = ExecutionClaimOwner()

    @property
    def graph(self) -> CompiledGraph[GraphValueT]:
        return self._graph

    def start_command(self, run_id: GraphRunId) -> StartGraphRun:
        return project_start_graph_command(self._graph, run_id)

    def validate_state(self, state: GraphRunState) -> None:
        require_snapshot_matches_graph(self._graph, state)
        scope = self._graph.definition_scope
        if not scope and state.parent is not None:
            raise SnapshotMismatchError("root graph state cannot carry a parent activation")
        if scope and (state.parent is None or state.parent.node_id != scope[-1]):
            raise SnapshotMismatchError("nested graph state does not match its compiled definition scope")

    def _validate_scope_run(
        self,
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
    ) -> None:
        if scope_run.scope != self._graph.definition_scope or state.run_id != scope_run.graph_run_id:
            raise SnapshotMismatchError("request scope does not match its compiled graph run")

    async def prepare(self, request: StepRequest[GraphValueT]) -> PrepareDisposition[GraphValueT]:
        self.validate_state(request.state)
        self._validate_scope_run(request.state, request.scope_run)
        return await prepare_superstep(self._claim_owner, self._graph, request)

    async def execute(
        self,
        claim: PreparedExecutionClaim,
        request: StepRequest[GraphValueT],
    ) -> GraphExecutionSession[GraphValueT]:
        self.validate_state(request.state)
        self._validate_scope_run(request.state, request.scope_run)
        validate_execution_session_request(self._graph, request, claim)
        consumed = await claim.consume(self._claim_owner, request.state, request.request_attempt_id)
        return issue_execution_session(self._graph, request, consumed)

    def resume(self, request: ResumeRequest[GraphValueT]) -> PreparedResume[GraphValueT]:
        state = request.state
        self.validate_state(state)
        self._validate_scope_run(state, request.scope_run)
        if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
            raise SnapshotMismatchError("resume requires one quiescent running graph")
        require_resume_input_binding(self._graph, state)
        requested_ids = tuple(action.node_id for action in request.actions)
        if (
            not requested_ids
            or requested_ids != tuple(sorted(requested_ids))
            or len(requested_ids) != len(set(requested_ids))
            or any(action.scope != request.scope_run.scope for action in request.actions)
        ):
            raise SnapshotMismatchError("resume actions must be non-empty, distinct, canonical, and scoped")
        actions: list[ResumeFailedNode | ResumeInterruptedNode | SkipFailedNode] = []
        replacements: dict[GraphNodeId, GraphNodeSettlement] = {}
        admitted_inputs: list[AdmittedResumeInput[GraphValueT]] = []
        for requested in request.actions:
            current = frontier_node(state.frontier, requested.node_id)
            if current is None:
                raise SnapshotMismatchError("resume request references an unknown frontier node")
            activation = StableActivation(request.scope_run, state.superstep, requested.node_id)
            descriptor = self._graph.materializations[requested.node_id].descriptor.identity
            if isinstance(requested, ResumeFailedNodeRequest):
                if not isinstance(current.settlement, FailedGraphNode):
                    raise SnapshotMismatchError("failure resume requires a failed node")
                if isinstance(requested.input, OverrideNodeInput):
                    binding = encode_resume_input(self._graph, requested.input.values)
                    frame = decode_resume_input(self._graph, requested.node_id, bytes(binding.payload))
                else:
                    binding = UseStepRequestInput()
                    frame = materialize_node_input(
                        self._graph,
                        replace(
                            state,
                            frontier=GraphFrontierState(
                                tuple(
                                    GraphFrontierNode(
                                        node.node_id,
                                        PendingGraphNode(UseStepRequestInput())
                                        if node.node_id == requested.node_id
                                        else node.settlement,
                                    )
                                    for node in state.frontier.nodes
                                )
                            ),
                        ),
                        request.scope_run,
                        request.frames,
                        requested.node_id,
                    )
                actions.append(ResumeFailedNode(requested.node_id, binding))
                replacements[requested.node_id] = PendingGraphNode(binding)
                admitted_inputs.append(
                    AdmittedResumeInput(
                        ResumeInputAvailabilityCoordinate(activation, descriptor),
                        frame,
                    )
                )
            elif isinstance(requested, ResumeInterruptedNodeRequest):
                if not isinstance(current.settlement, InterruptedGraphNode):
                    raise SnapshotMismatchError("interrupt resume requires an interrupted node")
                identity = current.settlement.interrupt.identity
                if requested.interrupt_id != graph_interrupt_id(
                    identity.run_id,
                    identity.superstep,
                    identity.node_id,
                    identity.execution_generation,
                ):
                    raise SnapshotMismatchError("interrupt resume ID does not match the current node interrupt")
                binding = encode_resume_input(self._graph, requested.input.values)
                frame = decode_resume_input(self._graph, requested.node_id, bytes(binding.payload))
                actions.append(ResumeInterruptedNode(requested.node_id, requested.interrupt_id, binding))
                replacements[requested.node_id] = PendingGraphNode(binding)
                admitted_inputs.append(
                    AdmittedResumeInput(
                        ResumeInputAvailabilityCoordinate(activation, descriptor),
                        frame,
                    )
                )
            else:
                if not isinstance(current.settlement, FailedGraphNode):
                    raise SnapshotMismatchError("skip requires a failed node")
                routing = (
                    ContinueGraphRouting()
                    if requested.route is None
                    else SelectGraphRoute(GraphRouteId(requested.route))
                )
                validate_routing_contribution(self._graph, requested.node_id, routing)
                reason = GraphSkipReason(requested.reason)
                actions.append(SkipFailedNode(requested.node_id, reason, routing))
                replacements[requested.node_id] = SkippedGraphNode(
                    current.settlement.failure,
                    reason,
                    routing,
                )
        simulated = GraphFrontierState(
            tuple(
                GraphFrontierNode(node.node_id, replacements.get(node.node_id, node.settlement))
                for node in state.frontier.nodes
            )
        )
        validate_graph_frontier(state, simulated)
        return PreparedResume(
            ResumeGraphNodes(state.revision, tuple(actions)),
            tuple(admitted_inputs),
        )


__all__: list[str] = []

"""Sole graph executor with explicit prepare, execute, and resume projections."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Generic, TypeVar

from mote_kernel.execution.claim import ExecutionClaimOwner, PreparedExecutionClaim
from mote_kernel.execution.engine.resume_input import encode_resume_input, require_resume_input_binding
from mote_kernel.execution.engine.routing import validate_routing_contribution
from mote_kernel.execution.engine.session import GraphExecutionSession, issue_execution_session
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import prepare_superstep, validate_execution_session_request
from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph import CompiledGraph, NestedGraphNodeDefinition, compile_graph
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
    SkipFailedNodeRequest,
    StepRequest,
    UseRequestInput,
)
from mote_kernel.execution.result import PrepareDisposition
from mote_kernel.state.graph_state import (
    FailedGraphNode,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFrontierNode,
    GraphFrontierState,
    GraphNodeId,
    GraphNodeSettlement,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    PendingGraphNode,
    ResumeFailedNode,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    SkipFailedNode,
    SkippedGraphNode,
    StartGraphRun,
    UseStepRequestInput,
    frontier_node,
    graph_interrupt_id,
)
from mote_kernel.state.graph_state.validation import validate_graph_frontier

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
GraphKey = tuple[GraphDefinitionId, GraphDefinitionVersion]


def _compile_graph_family(
    root: CompiledGraph[InputT, OutputT],
) -> tuple[
    Mapping[GraphKey, CompiledGraph[InputT, OutputT]],
    frozenset[tuple[GraphKey, GraphNodeId]],
]:
    graphs: dict[GraphKey, CompiledGraph[InputT, OutputT]] = {}
    parent_nodes: set[tuple[GraphKey, GraphNodeId]] = set()
    pending = {(root.definition_id, root.version): root}
    while pending:
        key, graph = pending.popitem()
        graphs[key] = graph
        for definition in graph.nodes.values():
            if isinstance(definition, NestedGraphNodeDefinition):
                child = compile_graph(definition.graph)
                child_key = (child.definition_id, child.version)
                parent_nodes.add((child_key, definition.node_id))
                if child_key not in graphs and child_key not in pending:
                    pending[child_key] = child
    return MappingProxyType(graphs), frozenset(parent_nodes)


class GraphExecutor(Generic[InputT, OutputT]):
    __slots__ = ("_claim_owner", "_graphs", "_parent_nodes", "_root_key")

    def __init__(self, graph: CompiledGraph[InputT, OutputT]) -> None:
        self._graphs, self._parent_nodes = _compile_graph_family(graph)
        self._root_key = (graph.definition_id, graph.version)
        self._claim_owner = ExecutionClaimOwner()

    def start_command(self, run_id: GraphRunId) -> StartGraphRun:
        return project_start_graph_command(self._graphs[self._root_key], run_id)

    def _graph_for_state(self, state: GraphRunState) -> CompiledGraph[InputT, OutputT]:
        key = (state.definition_id, state.definition_version)
        graph = self._graphs.get(key)
        if graph is None:
            raise SnapshotMismatchError("graph run is not owned by this graph executor")
        if key == self._root_key:
            if state.parent is not None:
                raise SnapshotMismatchError("root graph state cannot carry a parent activation")
        elif state.parent is None:
            raise SnapshotMismatchError("nested graph state requires a parent activation")
        return graph

    async def prepare(self, request: StepRequest[InputT, OutputT]) -> PrepareDisposition[InputT, OutputT]:
        graph = self._graph_for_state(request.state)
        require_snapshot_matches_graph(graph, request.state, self._parent_nodes)
        return await prepare_superstep(self._claim_owner, graph, request)

    async def execute(
        self,
        claim: PreparedExecutionClaim,
        request: StepRequest[InputT, OutputT],
    ) -> GraphExecutionSession[InputT, OutputT]:
        graph = self._graph_for_state(request.state)
        require_snapshot_matches_graph(graph, request.state, self._parent_nodes)
        validate_execution_session_request(graph, request, claim)
        consumed = await claim.consume(self._claim_owner, request.state, request.request_attempt_id)
        return issue_execution_session(graph, request, consumed, self._parent_nodes)

    def resume(self, request: ResumeRequest[InputT]) -> ResumeGraphNodes:
        state = request.state
        graph = self._graph_for_state(state)
        require_snapshot_matches_graph(graph, state, self._parent_nodes)
        if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
            raise SnapshotMismatchError("resume requires one quiescent running graph")
        require_resume_input_binding(graph, state)
        if any(
            not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                requested,
                ResumeFailedNodeRequest | ResumeInterruptedNodeRequest | SkipFailedNodeRequest,
            )
            for requested in request.actions
        ):
            raise SnapshotMismatchError("resume request has an unsupported action variant")
        requested_ids = tuple(action.node_id for action in request.actions)
        if (
            not requested_ids
            or requested_ids != tuple(sorted(requested_ids))
            or len(requested_ids) != len(set(requested_ids))
        ):
            raise SnapshotMismatchError("resume actions must be non-empty, distinct, and canonical")
        actions: list[ResumeFailedNode | ResumeInterruptedNode | SkipFailedNode] = []
        replacements: dict[GraphNodeId, GraphNodeSettlement] = {}
        for requested in request.actions:
            current = frontier_node(state.frontier, requested.node_id)
            if current is None:
                raise SnapshotMismatchError("resume request references an unknown frontier node")
            if isinstance(requested, ResumeFailedNodeRequest):
                if not isinstance(current.settlement, FailedGraphNode):
                    raise SnapshotMismatchError("failure resume requires a failed node")
                if isinstance(requested.input, OverrideNodeInput):
                    binding = encode_resume_input(graph, requested.input.value)
                elif isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                    requested.input, UseRequestInput
                ):
                    binding = UseStepRequestInput()
                else:
                    raise SnapshotMismatchError("failure resume input has an unsupported variant")
                action = ResumeFailedNode(requested.node_id, binding)
                replacement = PendingGraphNode(binding)
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
                if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                    requested.input, OverrideNodeInput
                ):
                    raise SnapshotMismatchError("interrupt resume input has an unsupported variant")
                binding = encode_resume_input(graph, requested.input.value)
                action = ResumeInterruptedNode(requested.node_id, requested.interrupt_id, binding)
                replacement = PendingGraphNode(binding)
            else:
                validate_routing_contribution(graph, requested.node_id, requested.routing)
                action = SkipFailedNode(requested.node_id, requested.reason, requested.routing)
                if not isinstance(current.settlement, FailedGraphNode):
                    raise SnapshotMismatchError("skip requires a failed node")
                replacement = SkippedGraphNode(
                    current.settlement.failure,
                    requested.reason,
                    requested.routing,
                )
            actions.append(action)
            replacements[requested.node_id] = replacement
        simulated = GraphFrontierState(
            tuple(
                GraphFrontierNode(node.node_id, replacements.get(node.node_id, node.settlement))
                for node in state.frontier.nodes
            )
        )
        validate_graph_frontier(state, simulated)
        return ResumeGraphNodes(state.revision, tuple(actions))


__all__ = ["GraphExecutor"]

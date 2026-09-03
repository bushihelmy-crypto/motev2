"""Pure preparation of interrupted-node resumes."""

from typing import TypeVar

from mote_kernel.execution.engine.resume_input import (
    _require_node_materialization,
    _resume_input_coordinate,
    decode_resume_input,
    encode_resume_input,
    require_resume_input_binding,
)
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph.ports import CompiledActivationRule
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import NodeInputFrame
from mote_kernel.execution.identity import stable_activation
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
)
from mote_kernel.execution.result import PreparedResume
from mote_kernel.execution.run_context import AdmittedResumeInput
from mote_kernel.state.graph_state import (
    GraphActivationIdentity,
    GraphNodeId,
    GraphRunStatus,
    InterruptedGraphNode,
    OverrideGraphNodeInput,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    frontier_node,
    graph_interrupt_id,
)

GraphValueT = TypeVar("GraphValueT")


def _admit_override_resume_input(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
    override: OverrideNodeInput[GraphValueT],
) -> tuple[OverrideGraphNodeInput, NodeInputFrame[GraphValueT]]:
    binding = encode_resume_input(graph, override.values)
    frame = decode_resume_input(graph, node_id, bytes(binding.payload))
    return binding, frame


def prepare_resume(
    graph: CompiledGraph[GraphValueT],
    request: ResumeRequest[GraphValueT],
) -> PreparedResume[GraphValueT]:
    """Prepare one resume without creating a live execution owner."""

    state = request.state
    require_scoped_snapshot_matches_graph(graph, state, request.scope_run)
    if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
        raise SnapshotMismatchError("resume requires one quiescent running graph")
    require_resume_input_binding(graph, state)
    if any(type(action) is not ResumeInterruptedNodeRequest for action in request.actions):
        raise SnapshotMismatchError("resume request has an unsupported action")
    requested_ids = tuple(action.node_id for action in request.actions)
    if (
        not requested_ids
        or requested_ids != tuple(sorted(set(requested_ids)))
        or any(action.scope != request.scope_run.scope for action in request.actions)
    ):
        raise SnapshotMismatchError("resume actions must be non-empty, distinct, canonical, and scoped")
    actions: list[ResumeInterruptedNode] = []
    admitted_inputs: list[AdmittedResumeInput[GraphValueT]] = []
    for requested in request.actions:
        current = frontier_node(state.frontier, requested.node_id)
        if current is None:
            raise SnapshotMismatchError("resume request references an unknown frontier node")
        activation = stable_activation(
            request.scope_run,
            GraphActivationIdentity(state.run_id, state.superstep, requested.node_id),
        )
        plan = _require_node_materialization(graph, requested.node_id)
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
        if any(isinstance(binding.source, CompiledActivationRule) for binding in plan.bindings.entries):
            raise SnapshotMismatchError("feedback activation cannot use an input override")
        binding, frame = _admit_override_resume_input(graph, requested.node_id, requested.input)
        actions.append(ResumeInterruptedNode(requested.node_id, requested.interrupt_id, binding))
        admitted_inputs.append(
            AdmittedResumeInput(
                _resume_input_coordinate(activation, plan),
                frame,
            )
        )
    return PreparedResume(
        ResumeGraphNodes(state.revision, tuple(actions)),
        tuple(admitted_inputs),
    )


__all__: list[str] = []

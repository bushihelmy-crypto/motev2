from dataclasses import replace
from typing import cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.resume_admission import prepare_resume
from mote_kernel.execution.errors import GraphValueAdmissionError, SnapshotMismatchError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import _frame_value
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import root_scope_run
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeInterruptedNodeRequest,
    ResumeNodeRequest,
    ResumeRequest,
)
from mote_kernel.execution.run_context import ResumeInputAvailabilityCoordinate, ScopedFrameIndex
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ActivationReference,
    ClaimGraphExecution,
    GraphAbortReason,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptId,
    GraphInterruptPayload,
    GraphNodeId,
    GraphNodeInterrupt,
    GraphNodeInterruptIdentity,
    GraphResumeInputCodecId,
    GraphResumeInputPayload,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    InterruptedGraphNode,
    InterruptedGraphNodeOutcome,
    OverrideGraphNodeInput,
    PendingGraphNode,
    ResumeGraphNodes,
    ResumeInterruptedNode,
    RoutedActivationCause,
    SettleGraphNode,
    frontier_node,
    graph_interrupt_id,
    reduce_graph_run,
)


async def _node(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values()


class _Codec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


class _TrackingCodec(_Codec):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.tamper: str | None = None

    def encode(self, value: Graph.Values[str]) -> bytes:
        self.events.append("encode")
        return super().encode(value)

    def decode(self, payload: bytes) -> Graph.Values[str]:
        self.events.append("decode")
        if self.tamper == "name":
            return Graph.values(other="answer")
        if self.tamper == "type":
            return cast(Graph.Values[str], Graph.values(value=True))
        return super().decode(payload)


def interruptible_graph(
    *node_ids: str,
    codec: bool = True,
    codec_implementation: _Codec | None = None,
) -> CompiledGraph[str]:
    binding: ResumeInputBinding[str] | None = None
    if codec:
        implementation = _Codec() if codec_implementation is None else codec_implementation
        binding = ResumeInputBinding(
            GraphResumeInputCodecId("resume.input.v1"),
            1,
            implementation,
            implementation,
        )
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resume.admission"),
            GraphDefinitionVersion(1),
            tuple(
                CallableNodeDefinition(
                    GraphNodeId(node_id),
                    _node,
                    normalize_input_bindings({"value": Graph.graph_input("value", str)}),
                    normalize_output_declarations({}),
                )
                for node_id in node_ids
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
            resume_input=binding,
        )
    )


def predecessor_interruptible_graph() -> CompiledGraph[str]:
    initialize_id = GraphNodeId("initialize")
    loop_id = GraphNodeId("loop")
    initialize = CallableNodeDefinition(
        initialize_id,
        _node,
        normalize_input_bindings({"value": Graph.graph_input("seed", str)}),
        normalize_output_declarations({"value": str}),
    )
    loop = CallableNodeDefinition(
        loop_id,
        _node,
        normalize_input_bindings({"value": Graph.node_output("value")}),
        normalize_output_declarations({"value": str}),
    )
    codec = _Codec()
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resume.predecessor"),
            GraphDefinitionVersion(1),
            (initialize, loop),
            (
                DirectEdge(initialize_id, loop_id),
                ConditionalEdge(loop_id, GraphRouteId("continue"), loop_id),
                ConditionalEdge(loop_id, GraphRouteId("done"), END),
            ),
            (),
            normalize_graph_output_declarations({"value": Graph.node_output("loop", "value")}),
            resume_input=ResumeInputBinding(
                GraphResumeInputCodecId("resume.input.v1"),
                1,
                codec,
                codec,
            ),
        )
    )


def interrupted_predecessor_state(graph: CompiledGraph[str]) -> GraphRunState:
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    initialize_id = GraphNodeId("initialize")
    loop_id = GraphNodeId("loop")
    predecessor = ActivationReference(
        GraphActivationIdentity(state.run_id, 0, initialize_id),
        None,
    )
    return replace(
        state,
        superstep=1,
        execution_sequence=1,
        frontier=GraphFrontierState(
            (
                GraphFrontierNode(
                    loop_id,
                    InterruptedGraphNode(
                        GraphNodeInterrupt(
                            GraphNodeInterruptIdentity(state.run_id, 1, loop_id, 1),
                            GraphInterruptPayload(b"question-loop"),
                        )
                    ),
                    RoutedActivationCause((predecessor,)),
                ),
            )
        ),
        settled_activations=(predecessor,),
    )


def interrupted_state(graph: CompiledGraph[str]) -> GraphRunState:
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("claim"), None),
    )
    assert claimed.execution is not None
    current = claimed
    generation = claimed.execution.token.generation
    for node_id in tuple(graph.nodes):
        assert current.execution is not None
        current = reduce_graph_run(
            current,
            SettleGraphNode(
                current.revision,
                current.execution.token,
                InterruptedGraphNodeOutcome(
                    node_id,
                    GraphNodeInterruptIdentity(current.run_id, current.superstep, node_id, generation),
                    GraphInterruptPayload(f"question-{node_id}".encode()),
                ),
            ),
        )
    return current


def exact_interrupt_id(state: GraphRunState, node_id: GraphNodeId) -> GraphInterruptId:
    node = frontier_node(state.frontier, node_id)
    assert node is not None and isinstance(node.settlement, InterruptedGraphNode)
    identity = node.settlement.interrupt.identity
    return graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )


def request_action(
    state: GraphRunState,
    node_id: GraphNodeId,
    value: str,
) -> ResumeInterruptedNodeRequest[str]:
    return ResumeInterruptedNodeRequest(
        (),
        node_id,
        exact_interrupt_id(state, node_id),
        OverrideNodeInput(Graph.values(value=value)),
    )


def test_prepare_resume_admits_one_exact_interrupt_input_and_command() -> None:
    graph = interruptible_graph("node")
    state = interrupted_state(graph)
    scope_run = root_scope_run(state.run_id)

    prepared = prepare_resume(
        graph,
        ResumeRequest(state, scope_run, ScopedFrameIndex(), (request_action(state, GraphNodeId("node"), "answer"),)),
    )

    assert prepared.command == ResumeGraphNodes(
        state.revision,
        (
            ResumeInterruptedNode(
                GraphNodeId("node"),
                exact_interrupt_id(state, GraphNodeId("node")),
                OverrideGraphNodeInput(GraphResumeInputPayload(b"answer")),
            ),
        ),
    )
    assert len(prepared.inputs) == 1
    admitted = prepared.inputs[0]
    assert admitted.coordinate == ResumeInputAvailabilityCoordinate(
        prepared.inputs[0].coordinate.activation,
        graph.transition.materializations[GraphNodeId("node")].descriptor.identity,
    )
    assert _frame_value(admitted.frame, "value") == "answer"

    successor = reduce_graph_run(state, prepared.command)
    assert successor.frontier.nodes[0].settlement == PendingGraphNode(
        OverrideGraphNodeInput(GraphResumeInputPayload(b"answer"))
    )


def test_prepare_resume_admits_multiple_interrupts_in_canonical_order() -> None:
    graph = interruptible_graph("a", "b")
    state = interrupted_state(graph)
    actions = (
        request_action(state, GraphNodeId("a"), "first"),
        request_action(state, GraphNodeId("b"), "second"),
    )

    prepared = prepare_resume(
        graph,
        ResumeRequest(state, root_scope_run(state.run_id), ScopedFrameIndex(), actions),
    )

    assert tuple(action.node_id for action in prepared.command.actions) == (GraphNodeId("a"), GraphNodeId("b"))
    assert tuple(_frame_value(admitted.frame, "value") for admitted in prepared.inputs) == ("first", "second")


@pytest.mark.parametrize("case", ["empty", "reverse", "duplicate", "wrong-scope"])
def test_prepare_resume_rejects_noncanonical_action_groups(case: str) -> None:
    graph = interruptible_graph("a", "b")
    state = interrupted_state(graph)
    first = request_action(state, GraphNodeId("a"), "first")
    second = request_action(state, GraphNodeId("b"), "second")
    actions: tuple[ResumeNodeRequest[str], ...]
    if case == "empty":
        actions = ()
    elif case == "reverse":
        actions = (second, first)
    elif case == "duplicate":
        actions = (first, first)
    else:
        actions = (replace(first, scope=(GraphNodeId("child"),)),)

    with pytest.raises(SnapshotMismatchError, match="non-empty, distinct, canonical, and scoped"):
        prepare_resume(
            graph,
            ResumeRequest(state, root_scope_run(state.run_id), ScopedFrameIndex(), actions),
        )


def test_prepare_resume_rejects_wrong_interrupt_identity_without_state_mutation() -> None:
    graph = interruptible_graph("node")
    state = interrupted_state(graph)
    action = replace(
        request_action(state, GraphNodeId("node"), "answer"),
        interrupt_id=GraphInterruptId("wrong"),
    )

    with pytest.raises(SnapshotMismatchError, match="does not match"):
        prepare_resume(
            graph,
            ResumeRequest(state, root_scope_run(state.run_id), ScopedFrameIndex(), (action,)),
        )
    assert state.status is GraphRunStatus.RUNNING
    assert isinstance(state.frontier.nodes[0].settlement, InterruptedGraphNode)


def test_prepare_resume_requires_a_current_interrupted_node() -> None:
    graph = interruptible_graph("node")
    state = interrupted_state(graph)
    pending = replace(
        state,
        frontier=replace(
            state.frontier,
            nodes=(
                replace(
                    state.frontier.nodes[0],
                    settlement=PendingGraphNode(OverrideGraphNodeInput(GraphResumeInputPayload(b"answer"))),
                ),
            ),
        ),
    )

    with pytest.raises(SnapshotMismatchError, match="interrupted node"):
        prepare_resume(
            graph,
            ResumeRequest(
                pending,
                root_scope_run(pending.run_id),
                ScopedFrameIndex(),
                (request_action(state, GraphNodeId("node"), "answer"),),
            ),
        )


def test_prepare_resume_rejects_an_override_for_a_predecessor_bound_activation() -> None:
    graph = predecessor_interruptible_graph()
    state = interrupted_predecessor_state(graph)
    action = request_action(state, GraphNodeId("loop"), "answer")

    with pytest.raises(SnapshotMismatchError, match="predecessor-bound activation cannot use an input override"):
        prepare_resume(
            graph,
            ResumeRequest(
                state,
                root_scope_run(state.run_id),
                ScopedFrameIndex(),
                (action,),
            ),
        )


def test_prepare_resume_requires_matching_compiled_codec_and_running_state() -> None:
    graph = interruptible_graph("node")
    state = interrupted_state(graph)
    action = request_action(state, GraphNodeId("node"), "answer")

    aborted = reduce_graph_run(
        state,
        AbortGraphRun(state.revision, GraphAbortReason("operator aborted")),
    )
    with pytest.raises(SnapshotMismatchError, match="quiescent running"):
        prepare_resume(
            graph,
            ResumeRequest(
                aborted,
                root_scope_run(state.run_id),
                ScopedFrameIndex(),
                (action,),
            ),
        )

    graph_without_codec = interruptible_graph("node", codec=False)
    with pytest.raises(SnapshotMismatchError, match="codec"):
        prepare_resume(
            graph_without_codec,
            ResumeRequest(state, root_scope_run(state.run_id), ScopedFrameIndex(), (action,)),
        )


def test_prepare_resume_rejects_an_unsupported_request_variant() -> None:
    graph = interruptible_graph("node")
    state = interrupted_state(graph)
    forged = cast(ResumeNodeRequest[str], object())

    with pytest.raises(SnapshotMismatchError, match="unsupported action"):
        prepare_resume(
            graph,
            ResumeRequest(state, root_scope_run(state.run_id), ScopedFrameIndex(), (forged,)),
        )


def test_resume_identity_and_shape_validation_precede_codec_execution() -> None:
    codec = _TrackingCodec()
    graph = interruptible_graph("node", codec_implementation=codec)
    state = interrupted_state(graph)
    frames: ScopedFrameIndex[str] = ScopedFrameIndex()
    scope_run = root_scope_run(state.run_id)
    exact = request_action(state, GraphNodeId("node"), "answer")

    stale = replace(exact, interrupt_id=GraphInterruptId("stale"))
    with pytest.raises(SnapshotMismatchError, match="does not match"):
        prepare_resume(graph, ResumeRequest(state, scope_run, frames, (stale,)))
    assert codec.events == []

    missing = replace(exact, node_id=GraphNodeId("missing"))
    with pytest.raises(SnapshotMismatchError, match="unknown frontier node"):
        prepare_resume(graph, ResumeRequest(state, scope_run, frames, (missing,)))
    assert codec.events == []

    prepared = prepare_resume(graph, ResumeRequest(state, scope_run, frames, (exact,)))
    assert codec.events == ["encode", "decode"]
    assert _frame_value(prepared.inputs[0].frame, "value") == "answer"


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("name", "node input names do not match the compiled descriptor"),
        ("type", "does not have its exact declared type"),
    ],
)
def test_resume_codec_output_is_readmitted_before_state_mutation(tamper: str, message: str) -> None:
    codec = _TrackingCodec()
    codec.tamper = tamper
    graph = interruptible_graph("node", codec_implementation=codec)
    state = interrupted_state(graph)
    frames: ScopedFrameIndex[str] = ScopedFrameIndex()
    request = ResumeRequest(
        state,
        root_scope_run(state.run_id),
        frames,
        (request_action(state, GraphNodeId("node"), "answer"),),
    )

    with pytest.raises(GraphValueAdmissionError, match=message):
        prepare_resume(graph, request)

    assert codec.events == ["encode", "decode"]
    assert request.state is state
    assert request.frames is frames
    assert isinstance(state.frontier.nodes[0].settlement, InterruptedGraphNode)

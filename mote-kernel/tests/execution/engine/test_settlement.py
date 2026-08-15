from dataclasses import replace

import pytest
from tests.execution.engine.factories import conditional, direct, leased_state, running_state, topology

from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.settlement import settle_result
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import InvalidRoutingCommandError, ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.graph import (
    CompiledGraph,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    NodeDefinition,
    NodeSuccess,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskSuccess
from mote_kernel.state.graph_state import (
    ContinueGraphRouting,
    GraphFailure,
    GraphInterruptPayload,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunState,
    SettleGraphNode,
)


def planned(graph: CompiledGraph[str, str], state: GraphRunState | None = None) -> tuple[GraphTask, GraphRunState]:
    current = running_state() if state is None else state
    return plan_tasks(graph, current, ExecutionLimits())[0], leased_state(current)


def test_success_projects_only_one_settlement_command() -> None:
    graph = topology("a", "b", edges=(direct("a", "b"),))
    task, state = planned(graph, running_state(frontier=("a",)))
    command = settle_result(graph, state, TaskSuccess(task, "output", ContinueGraphRouting()))
    assert command == SettleGraphNode(state.revision, state.execution.token, command.outcome)  # type: ignore[union-attr]
    assert command.outcome.node_id == GraphNodeId("a")


def test_failure_does_not_inline_routing_resolution() -> None:
    graph = topology("a")
    task, state = planned(graph)
    command = settle_result(graph, state, TaskFailure(task, GraphFailure("failed")))
    assert command.outcome.node_id == GraphNodeId("a")


def test_success_routing_is_validated_at_projection_boundary() -> None:
    graph = topology("a", "next", edges=(conditional("a", "go", "next"),))
    task, state = planned(graph)
    with pytest.raises(InvalidRoutingCommandError):
        settle_result(graph, state, TaskSuccess(task, "output", ContinueGraphRouting()))


def test_interrupt_requires_a_compiled_resume_codec() -> None:
    graph = topology("a")
    task, state = planned(graph)
    with pytest.raises(ResultCollectionError, match="codec"):
        settle_result(graph, state, TaskInterrupt(task, GraphInterruptPayload(b"question")))


def test_interrupt_projection_uses_current_generation() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    class Codec:
        def encode(self, value: str) -> bytes:
            return value.encode()

        def decode(self, payload: bytes) -> str:
            return payload.decode()

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), node),),
            (),
            (GraphNodeId("a"),),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input"), 1, codec, codec),
        )
    )
    state = replace(
        running_state(definition_id="graph"),
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input"), 1),
    )
    task, state = planned(graph, state)
    command = settle_result(graph, state, TaskInterrupt(task, GraphInterruptPayload(b"question")))
    assert command.execution.generation == state.execution.token.generation  # type: ignore[union-attr]


def test_settlement_requires_a_committed_matching_snapshot_and_lease() -> None:
    graph = topology("a")
    task = plan_tasks(graph, running_state(), ExecutionLimits())[0]
    with pytest.raises(ResultCollectionError, match="lease"):
        settle_result(graph, running_state(), TaskSuccess(task, "output", ContinueGraphRouting()))
    with pytest.raises(SnapshotMismatchError):
        settle_result(
            graph,
            leased_state(running_state(definition_id="other")),
            TaskSuccess(task, "output", ContinueGraphRouting()),
        )


def test_falsy_output_is_not_copied_into_the_state_command() -> None:
    async def node(node_input: str) -> NodeSuccess[bool]:
        return NodeSuccess(bool(node_input))

    graph = compile_graph(
        GraphDefinition[str, bool](
            GraphDefinitionId("boolean.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), node),),
            (),
            (GraphNodeId("a"),),
        )
    )
    current = running_state(definition_id="boolean.graph")
    task = plan_tasks(graph, current, ExecutionLimits())[0]
    state = leased_state(current)
    result = TaskSuccess(task, False, ContinueGraphRouting())
    command = settle_result(graph, state, result)
    assert command.outcome.node_id == task.node_id


def test_task_coordinates_are_canonical() -> None:
    graph = topology("a")
    task, state = planned(graph)
    forged = replace(task, task_id="forged")  # type: ignore[arg-type]
    with pytest.raises(ResultCollectionError, match="coordinates"):
        settle_result(graph, state, TaskSuccess(forged, "output", ContinueGraphRouting()))

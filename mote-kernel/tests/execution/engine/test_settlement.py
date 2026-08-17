from dataclasses import replace
from typing import TypeVar

import pytest
from tests.execution.engine.factories import (
    callable_node,
    conditional,
    direct,
    leased_state,
    running_state,
    task_success,
    topology,
)

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.settlement import settle_result
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import InvalidRoutingCommandError, ResultCollectionError, SnapshotMismatchError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskInterrupt
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunState,
    SettleGraphNode,
)

GraphValueT = TypeVar("GraphValueT")


def planned(graph: CompiledGraph[GraphValueT], state: GraphRunState | None = None) -> tuple[GraphTask, GraphRunState]:
    current = running_state() if state is None else state
    return plan_tasks(graph, current, ExecutionLimits())[0], leased_state(current)


def test_success_projects_only_one_settlement_command() -> None:
    graph = topology("a", "b", edges=(direct("a", "b"),))
    task, state = planned(graph, running_state(frontier=("a",)))
    command = settle_result(graph, state, task_success(task, "output"))
    assert state.execution is not None
    assert command == SettleGraphNode(state.revision, state.execution.token, command.outcome)
    assert command.outcome.node_id == GraphNodeId("a")


def test_failure_does_not_inline_routing_resolution() -> None:
    graph = topology("a")
    task, state = planned(graph)
    command = settle_result(graph, state, TaskFailure(task, "failed"))
    assert command.outcome.node_id == GraphNodeId("a")


def test_success_routing_is_validated_at_projection_boundary() -> None:
    graph = topology("a", "next", edges=(conditional("a", "go", "next"),))
    task, state = planned(graph)
    with pytest.raises(InvalidRoutingCommandError):
        settle_result(graph, state, task_success(task, "output"))


def test_interrupt_requires_a_compiled_resume_codec() -> None:
    graph = topology("a")
    task, state = planned(graph)
    with pytest.raises(ResultCollectionError, match="codec"):
        settle_result(graph, state, TaskInterrupt(task, b"question"))


def test_interrupt_projection_uses_current_generation() -> None:
    class Codec:
        def encode(self, value: Graph.Values[str]) -> bytes:
            return value["value"].encode()

        def decode(self, payload: bytes) -> Graph.Values[str]:
            return Graph.values(value=payload.decode())

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            (callable_node("a"),),
            (),
            (),
            normalize_graph_output_declarations({}),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input"), 1, codec, codec),
        )
    )
    state = replace(
        running_state(definition_id="graph"),
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input"), 1),
    )
    task, state = planned(graph, state)
    command = settle_result(graph, state, TaskInterrupt(task, b"question"))
    assert state.execution is not None
    assert command.execution.generation == state.execution.token.generation


def test_settlement_requires_a_committed_matching_snapshot_and_lease() -> None:
    graph = topology("a")
    task = plan_tasks(graph, running_state(), ExecutionLimits())[0]
    with pytest.raises(ResultCollectionError, match="lease"):
        settle_result(graph, running_state(), task_success(task, "output"))
    with pytest.raises(SnapshotMismatchError):
        settle_result(
            graph,
            leased_state(running_state(definition_id="other")),
            task_success(task, "output"),
        )


def test_falsy_output_is_not_copied_into_the_state_command() -> None:
    async def node(values: Graph.Values[bool]) -> Graph.Values[bool]:
        return values

    graph = compile_graph(
        GraphDefinition[bool](
            GraphDefinitionId("boolean.graph"),
            GraphDefinitionVersion(1),
            (
                CallableNodeDefinition(
                    GraphNodeId("a"),
                    node,
                    normalize_input_bindings({"value": Graph.graph_input("value", bool)}),
                    normalize_output_declarations({"value": bool}),
                ),
            ),
            (),
            (),
            normalize_graph_output_declarations({}),
        )
    )
    current = running_state(definition_id="boolean.graph")
    task = plan_tasks(graph, current, ExecutionLimits())[0]
    state = leased_state(current)
    result = task_success(task, False)
    command = settle_result(graph, state, result)
    assert command.outcome.node_id == task.node_id


def test_task_coordinates_are_canonical() -> None:
    graph = topology("a")
    task, state = planned(graph)
    forged = replace(task, task_id=TaskId("forged"))
    with pytest.raises(ResultCollectionError, match="coordinates"):
        settle_result(graph, state, task_success(forged, "output"))

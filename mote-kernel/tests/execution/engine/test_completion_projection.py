from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest
from tests.execution.engine.factories import (
    callable_node,
    compiled_graph,
    leased_state,
    output_value,
    running_state,
    task_success,
    terminal_state,
)

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.settlement import settle_result
from mote_kernel.execution.engine.task import GraphTask, TaskId, task_identity
from mote_kernel.execution.errors import InvalidRoutingCommandError, ResultCollectionError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.ports import normalize_graph_output_declarations
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import TaskFailure, TaskInterrupt, TaskResult, TaskSuccess
from mote_kernel.state.graph_state import (
    FailedGraphNode,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFailure,
    GraphNodeId,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunId,
    GraphRunStatus,
    InterruptedGraphNodeOutcome,
    PendingGraphNode,
    SettleGraphNode,
    SucceededGraphNode,
    reduce_graph_run,
)


def planned():
    state = running_state(frontier=("a", "b"))
    graph = compiled_graph("a", "b", entries=("a", "b"))
    return graph, state, plan_tasks(graph, state, ExecutionLimits())


def test_one_typed_completion_projects_one_command_without_waiting_for_siblings() -> None:
    graph, state, tasks = planned()
    leased = leased_state(state)
    command = settle_result(graph, leased, task_success(tasks[0], "output"))
    assert isinstance(command, SettleGraphNode)
    assert command.outcome.node_id == GraphNodeId("a")
    assert command.expected_revision == leased.revision


@pytest.mark.parametrize("result, settlement_type", [("success", SucceededGraphNode), ("failure", FailedGraphNode)])
def test_single_result_variants_are_state_owned(
    result: str,
    settlement_type: type[SucceededGraphNode] | type[FailedGraphNode],
) -> None:
    graph, state, tasks = planned()
    if result == "success":
        value: TaskResult[str] = task_success(tasks[0], "output")
    else:
        value = TaskFailure(tasks[0], "failed")
    command = settle_result(graph, leased_state(state), value)
    next_state = reduce_graph_run(leased_state(state), command)
    assert isinstance(next_state.frontier.nodes[0].settlement, settlement_type)


def test_interrupt_result_projects_a_structured_identity() -> None:
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
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    state = running_state(definition_id="graph")
    state = replace(
        state,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
    )
    task = plan_tasks(graph, state, ExecutionLimits())[0]
    leased = leased_state(state)
    command = settle_result(graph, leased, TaskInterrupt(task, b"question"))
    assert isinstance(command.outcome, InterruptedGraphNodeOutcome)


def test_invalid_coordinates_and_nonpending_result_fail_closed() -> None:
    graph, state, tasks = planned()
    leased = leased_state(state)
    forged = replace(tasks[0], task_id=TaskId("forged"))
    with pytest.raises(ResultCollectionError, match="coordinates"):
        settle_result(graph, leased, task_success(forged, "output"))
    command = settle_result(graph, leased, task_success(tasks[0], "output"))
    settled = reduce_graph_run(leased, command)
    with pytest.raises(ResultCollectionError, match="pending"):
        settle_result(graph, settled, task_success(tasks[0], "again"))


@pytest.mark.parametrize("output", [None, False, 0, "", ()])
def test_falsy_output_remains_transient(output: object) -> None:
    graph, state, tasks = planned()
    result = task_success(tasks[0], output)
    command = settle_result(graph, leased_state(state), cast(TaskResult[str], result))
    assert output_value(result.output) == output
    assert command == settle_result(
        graph,
        leased_state(state),
        task_success(tasks[0], "different"),
    )


def test_task_result_union_uses_closed_nominal_variants() -> None:
    _graph, _state, tasks = planned()
    assert isinstance(task_success(tasks[0], "output"), TaskSuccess)
    assert isinstance(TaskFailure(tasks[0], "failed"), TaskFailure)
    assert isinstance(TaskInterrupt(tasks[0], b"question"), TaskInterrupt)


def test_unsupported_result_variant_fails_closed() -> None:
    graph, state, _tasks = planned()

    with pytest.raises(ResultCollectionError, match="unsupported variant"):
        settle_result(graph, leased_state(state), cast(TaskResult[str], object()))


@pytest.mark.parametrize("status", [GraphRunStatus.COMPLETED, GraphRunStatus.ABORTED])
def test_terminal_graph_cannot_accept_a_node_completion(status: GraphRunStatus) -> None:
    graph, _state, tasks = planned()

    with pytest.raises(ResultCollectionError, match="committed execution lease"):
        settle_result(
            graph,
            terminal_state(status),
            task_success(tasks[0], "output"),
        )


@pytest.mark.parametrize("coordinate", ["identity", "run", "superstep", "node"])
def test_each_forged_task_coordinate_is_rejected(coordinate: str) -> None:
    graph, state, tasks = planned()
    task = tasks[0]
    if coordinate == "identity":
        forged = replace(task, task_id=TaskId("forged"))
    elif coordinate == "run":
        run_id = GraphRunId("other")
        forged = replace(task, run_id=run_id, task_id=task_identity(run_id, task.superstep, task.node_id))
    elif coordinate == "superstep":
        forged = replace(
            task,
            superstep=task.superstep + 1,
            task_id=task_identity(task.run_id, task.superstep + 1, task.node_id),
        )
    else:
        forged = replace(task, node_id=tasks[1].node_id)

    with pytest.raises(ResultCollectionError, match="coordinates"):
        settle_result(
            graph,
            leased_state(state),
            task_success(forged, "output"),
        )


def test_completion_requires_the_committed_execution_token() -> None:
    graph, state, tasks = planned()

    with pytest.raises(ResultCollectionError, match="committed execution lease"):
        settle_result(graph, state, task_success(tasks[0], "output"))


def test_interrupt_completion_requires_the_compiled_resume_codec() -> None:
    graph, state, tasks = planned()

    with pytest.raises(ResultCollectionError, match="resume input codec"):
        settle_result(
            graph,
            leased_state(state),
            TaskInterrupt(tasks[0], b"question"),
        )


def test_projected_node_command_is_immutable() -> None:
    graph, state, tasks = planned()
    command = settle_result(
        graph,
        leased_state(state),
        task_success(tasks[0], "output"),
    )

    with pytest.raises(FrozenInstanceError):
        command.expected_revision = 99  # type: ignore[misc]


def test_later_planned_node_can_settle_before_earlier_sibling() -> None:
    graph, state, tasks = planned()
    leased = leased_state(state)

    command = settle_result(
        graph,
        leased,
        task_success(tasks[1], "b-output"),
    )
    after = reduce_graph_run(leased, command)

    assert isinstance(after.frontier.nodes[0].settlement, PendingGraphNode)
    assert isinstance(after.frontier.nodes[1].settlement, SucceededGraphNode)


def test_each_failure_completion_is_preserved_in_its_own_revision() -> None:
    graph, state, tasks = planned()
    current = leased_state(state)

    first = settle_result(graph, current, TaskFailure(tasks[1], "b failed"))
    current = reduce_graph_run(current, first)
    second = settle_result(graph, current, TaskFailure(tasks[0], "a failed"))
    current = reduce_graph_run(current, second)

    assert current.frontier.nodes[0].settlement == FailedGraphNode(GraphFailure("a failed"))
    assert current.frontier.nodes[1].settlement == FailedGraphNode(GraphFailure("b failed"))
    assert current.revision == 2


def test_unknown_canonical_task_cannot_substitute_a_pending_task() -> None:
    graph, state, _tasks = planned()
    node_id = GraphNodeId("unknown")
    task = GraphTask(
        task_identity(state.run_id, state.superstep, node_id),
        state.run_id,
        state.superstep,
        node_id,
    )

    with pytest.raises(ResultCollectionError, match="pending node"):
        settle_result(
            graph,
            leased_state(state),
            task_success(task, "output"),
        )


def test_success_routing_is_validated_for_the_completed_node() -> None:
    graph, state, tasks = planned()

    with pytest.raises(InvalidRoutingCommandError):
        settle_result(
            graph,
            leased_state(state),
            task_success(tasks[0], "output", route="missing"),
        )


def test_interrupt_projection_uses_the_current_execution_generation() -> None:
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
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    state = replace(
        running_state(definition_id="graph"),
        execution_sequence=6,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
    )
    leased = leased_state(state)
    task = plan_tasks(graph, state, ExecutionLimits())[0]

    command = settle_result(graph, leased, TaskInterrupt(task, b"question"))

    assert isinstance(command.outcome, InterruptedGraphNodeOutcome)
    assert command.outcome.identity.execution_generation == 7

from dataclasses import FrozenInstanceError, fields, replace

import pytest
from tests.execution.engine.factories import callable_node, compiled_graph, running_state, terminal_state

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.planner import plan_tasks
from mote_kernel.execution.engine.task import GraphTask, task_identity
from mote_kernel.execution.errors import ExecutionLimitError, InvalidExecutionSnapshotError, SnapshotMismatchError
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import DirectEdge
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.state.graph_state import (
    ActivationReference,
    ContinueGraphRouting,
    FailedGraphNode,
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphFailure,
    GraphFrontierNode,
    GraphFrontierState,
    GraphInterruptPayload,
    GraphJoinProgress,
    GraphNodeId,
    GraphNodeInterrupt,
    GraphNodeInterruptIdentity,
    GraphResumeInputCodec,
    GraphResumeInputCodecId,
    GraphRunId,
    GraphRunStatus,
    InterruptedGraphNode,
    SucceededGraphNode,
    child_graph_run_id,
)

LIMITS = ExecutionLimits()


def test_planner_materializes_only_pending_nodes_in_canonical_order() -> None:
    state = running_state(frontier=("a", "b", "c"))
    state = replace(
        state,
        frontier=GraphFrontierState(
            (
                state.frontier.nodes[0],
                GraphFrontierNode(
                    GraphNodeId("b"),
                    FailedGraphNode(GraphFailure("failed")),
                    state.frontier.nodes[1].cause,
                ),
                GraphFrontierNode(
                    GraphNodeId("c"),
                    SucceededGraphNode(ContinueGraphRouting()),
                    state.frontier.nodes[2].cause,
                ),
            )
        ),
    )

    tasks = plan_tasks(compiled_graph("a", "b", "c", entries=("a", "b", "c")), state, LIMITS)

    assert tuple(task.node_id for task in tasks) == (GraphNodeId("a"),)
    assert tasks[0].run_id == GraphRunId("run")
    assert tasks[0].superstep == 0
    assert tasks[0].task_id == task_identity(GraphRunId("run"), 0, GraphNodeId("a"))


def test_planner_excludes_every_nonpending_settlement_variant() -> None:
    class Codec:
        def encode(self, value: Graph.Values[str]) -> bytes:
            return value["value"].encode()

        def decode(self, payload: bytes) -> Graph.Values[str]:
            return Graph.values(value=payload.decode())

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            tuple(callable_node(node_id) for node_id in ("a", "b", "c", "d")),
            (),
            (),
            normalize_graph_output_declarations({}),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    state = running_state(frontier=("a", "b", "c", "d"))
    state = replace(
        state,
        execution_sequence=1,
        resume_input_codec=GraphResumeInputCodec(GraphResumeInputCodecId("input.v1"), 1),
        frontier=GraphFrontierState(
            (
                state.frontier.nodes[0],
                GraphFrontierNode(
                    GraphNodeId("b"),
                    FailedGraphNode(GraphFailure("failed")),
                    state.frontier.nodes[1].cause,
                ),
                GraphFrontierNode(
                    GraphNodeId("c"),
                    InterruptedGraphNode(
                        GraphNodeInterrupt(
                            GraphNodeInterruptIdentity(state.run_id, state.superstep, GraphNodeId("c"), 1),
                            GraphInterruptPayload(b"question"),
                        )
                    ),
                    state.frontier.nodes[2].cause,
                ),
                GraphFrontierNode(
                    GraphNodeId("d"),
                    SucceededGraphNode(ContinueGraphRouting()),
                    state.frontier.nodes[3].cause,
                ),
            )
        ),
    )

    tasks = plan_tasks(graph, state, LIMITS)

    assert tuple(task.node_id for task in tasks) == (GraphNodeId("a"),)


def test_planning_is_idempotent_and_task_projection_is_immutable() -> None:
    state = running_state(frontier=("a", "b"))
    graph = compiled_graph("a", "b", entries=("a", "b"))

    first = plan_tasks(graph, state, LIMITS)
    assert first == plan_tasks(graph, state, LIMITS)
    with pytest.raises(FrozenInstanceError):
        first[0].node_id = GraphNodeId("other")  # type: ignore[misc]
    assert {field.name for field in fields(GraphTask)} == {"task_id", "run_id", "superstep", "node_id"}


def test_graph_task_has_no_retry_or_attempt_policy() -> None:
    assert {field.name for field in fields(GraphTask)} == {"task_id", "run_id", "superstep", "node_id"}


def test_task_identity_changes_for_each_coordinate_and_handles_delimiter_content() -> None:
    values = {
        task_identity(GraphRunId("a:1"), 2, GraphNodeId("b")),
        task_identity(GraphRunId("a"), 1, GraphNodeId("2:b")),
        task_identity(GraphRunId("a"), 2, GraphNodeId("b")),
        task_identity(GraphRunId("a"), 2, GraphNodeId("c")),
    }
    assert len(values) == 4


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ((GraphRunId("first"), 0, GraphNodeId("a")), (GraphRunId("second"), 0, GraphNodeId("a"))),
        ((GraphRunId("run"), 0, GraphNodeId("a")), (GraphRunId("run"), 1, GraphNodeId("a"))),
        ((GraphRunId("run"), 0, GraphNodeId("a")), (GraphRunId("run"), 0, GraphNodeId("b"))),
    ],
)
def test_each_task_coordinate_changes_identity(
    left: tuple[GraphRunId, int, GraphNodeId], right: tuple[GraphRunId, int, GraphNodeId]
) -> None:
    assert task_identity(*left) != task_identity(*right)


def test_declaration_order_does_not_change_planned_batch() -> None:
    first = plan_tasks(
        compiled_graph("c", "a", "b", entries=("c", "a", "b")),
        running_state(frontier=("a", "b", "c")),
        LIMITS,
    )
    second = plan_tasks(
        compiled_graph("b", "c", "a", entries=("a", "b", "c")),
        running_state(frontier=("a", "b", "c")),
        LIMITS,
    )
    assert first == second


@pytest.mark.parametrize(
    "status",
    [GraphRunStatus.COMPLETED, GraphRunStatus.FAILED, GraphRunStatus.ABORTED],
)
def test_terminal_state_has_no_tasks(status: GraphRunStatus) -> None:
    assert plan_tasks(compiled_graph("a"), terminal_state(status), LIMITS) == ()


@pytest.mark.parametrize(
    "state",
    [running_state(definition_id="other"), running_state(version=2)],
)
def test_snapshot_identity_must_match_compiled_graph(state: object) -> None:
    with pytest.raises(SnapshotMismatchError):
        plan_tasks(compiled_graph("a"), state, LIMITS)  # type: ignore[arg-type]


def test_graph_identity_mismatch_takes_precedence_over_unknown_frontier() -> None:
    state = running_state(definition_id="other.graph", frontier=("unknown",))

    with pytest.raises(SnapshotMismatchError):
        plan_tasks(compiled_graph("a"), state, LIMITS)


def test_running_frontier_and_join_progress_must_belong_to_compiled_graph() -> None:
    with pytest.raises(InvalidExecutionSnapshotError, match="unknown nodes"):
        plan_tasks(compiled_graph("a"), running_state(frontier=("unknown",)), LIMITS)

    progress = GraphJoinProgress(
        (GraphNodeId("a"), GraphNodeId("b")),
        GraphNodeId("c"),
        (ActivationReference(GraphActivationIdentity(GraphRunId("run"), 0, GraphNodeId("a"))),),
    )
    with pytest.raises(InvalidExecutionSnapshotError, match="unknown join"):
        plan_tasks(
            compiled_graph("a", "b", "c", entries=("a", "b", "c")),
            running_state(superstep=1, frontier=("b",), join_progress=(progress,)),
            LIMITS,
        )


@pytest.mark.parametrize(
    ("max_supersteps", "max_parallel_tasks"),
    [(0, 64), (1_000, 0)],
)
def test_invalid_limits_fail_closed(max_supersteps: int, max_parallel_tasks: int) -> None:
    with pytest.raises(ExecutionLimitError):
        ExecutionLimits(max_supersteps, max_parallel_tasks)


@pytest.mark.parametrize("status", [GraphRunStatus.COMPLETED, GraphRunStatus.ABORTED])
def test_invalid_limits_fail_before_terminal_short_circuit(status: GraphRunStatus) -> None:
    with pytest.raises(ExecutionLimitError):
        plan_tasks(compiled_graph("a"), terminal_state(status), ExecutionLimits(max_parallel_tasks=0))


def test_superstep_limit_is_exact_and_parallel_limit_does_not_reject_frontier() -> None:
    graph = compiled_graph(
        "a",
        "b",
        entries=("a", "b"),
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("a")),
            DirectEdge(GraphNodeId("a"), END),
            DirectEdge(GraphNodeId("b"), GraphNodeId("b")),
            DirectEdge(GraphNodeId("b"), END),
        ),
    )
    with pytest.raises(ExecutionLimitError, match="superstep"):
        plan_tasks(graph, running_state(superstep=3, frontier=("a", "b")), ExecutionLimits(max_supersteps=3))
    assert len(plan_tasks(graph, running_state(frontier=("a", "b")), ExecutionLimits(max_parallel_tasks=1))) == 2
    assert len(plan_tasks(graph, running_state(frontier=("a", "b")), ExecutionLimits(max_parallel_tasks=2))) == 2


def test_last_allowed_superstep_is_plannable() -> None:
    graph = compiled_graph(
        "a",
        edges=(
            DirectEdge(GraphNodeId("a"), GraphNodeId("a")),
            DirectEdge(GraphNodeId("a"), END),
        ),
    )
    assert plan_tasks(graph, running_state(superstep=2), ExecutionLimits(max_supersteps=3))


def test_terminal_state_ignores_exhausted_superstep_limit() -> None:
    state = replace(terminal_state(GraphRunStatus.COMPLETED), superstep=10)
    assert plan_tasks(compiled_graph("a"), state, ExecutionLimits(max_supersteps=10)) == ()


def test_parent_linkage_does_not_change_task_projection() -> None:
    state = running_state()
    activation = GraphActivationIdentity(GraphRunId("parent"), 3, GraphNodeId("nested"))
    parent = replace(
        state,
        run_id=child_graph_run_id(activation.run_id, activation.superstep, activation.node_id),
        parent=activation,
    )
    parent_task = plan_tasks(compiled_graph("a"), parent, LIMITS)[0]
    root_task = plan_tasks(compiled_graph("a"), state, LIMITS)[0]

    assert (parent_task.superstep, parent_task.node_id) == (root_task.superstep, root_task.node_id)
    assert parent_task.run_id == parent.run_id
    assert root_task.run_id == state.run_id


def test_planner_accepts_large_deterministic_frontier_at_exact_limit() -> None:
    node_ids = tuple(f"node-{index:04d}" for index in range(256))
    tasks = plan_tasks(
        compiled_graph(*reversed(node_ids), entries=tuple(reversed(node_ids))),
        running_state(frontier=node_ids),
        ExecutionLimits(max_parallel_tasks=len(node_ids)),
    )
    assert tuple(task.node_id for task in tasks) == tuple(GraphNodeId(node_id) for node_id in node_ids)


def test_nested_definition_is_planned_as_one_parent_activation_without_invocation() -> None:
    calls = 0

    async def child_node(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return values

    child = GraphDefinition[str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (replace(callable_node("child"), operation=child_node),),
        (DirectEdge(GraphNodeId("child"), END),),
        (),
        normalize_graph_output_declarations({}),
    )
    parent = compile_graph(
        GraphDefinition[str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(
                    GraphNodeId("nested"),
                    child,
                    normalize_input_bindings({"value": Graph.graph_input("value", str)}),
                ),
            ),
            (DirectEdge(GraphNodeId("nested"), END),),
            (),
            normalize_graph_output_declarations({}),
        )
    )

    tasks = plan_tasks(parent, running_state(frontier=("nested",)), LIMITS)

    assert tuple(task.node_id for task in tasks) == (GraphNodeId("nested"),)
    assert calls == 0

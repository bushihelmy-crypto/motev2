from dataclasses import FrozenInstanceError, fields

import pytest
from tests.execution.engine.factories import compiled_graph, snapshot

from mote_kernel.execution.engine import GraphTask, plan_tasks
from mote_kernel.execution.engine.task import task_identity
from mote_kernel.execution.errors import ExecutionLimitError, InvalidExecutionSnapshotError, SnapshotMismatchError
from mote_kernel.execution.graph import (
    END,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeId,
    NodeSuccess,
    compile_graph,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.snapshot import ExecutionStatus, GraphRunId, JoinProgress

LIMITS = ExecutionLimits()


def test_planner_materializes_frontier_in_stable_order() -> None:
    execution_snapshot = snapshot(superstep=7, frontier=("c", "a", "b"))

    tasks = plan_tasks(compiled_graph("a", "b", "c", entries=("a", "b", "c")), execution_snapshot, LIMITS)

    assert tuple(task.node_id for task in tasks) == (NodeId("a"), NodeId("b"), NodeId("c"))
    assert all(task.run_id == GraphRunId("run") and task.superstep == 7 for task in tasks)
    assert tuple(task.task_id for task in tasks) == tuple(
        task_identity(GraphRunId("run"), 7, NodeId(node_id)) for node_id in ("a", "b", "c")
    )


def test_planner_is_idempotent_and_does_not_modify_snapshot() -> None:
    execution_snapshot = snapshot(frontier=("b", "a"))
    graph = compiled_graph("a", "b", entries=("a", "b"))

    assert plan_tasks(graph, execution_snapshot, LIMITS) == plan_tasks(graph, execution_snapshot, LIMITS)
    assert execution_snapshot.frontier == (NodeId("b"), NodeId("a"))
    with pytest.raises(FrozenInstanceError):
        execution_snapshot.superstep = 1  # type: ignore[misc]


def test_task_identity_is_unambiguous_for_delimiter_like_values() -> None:
    assert task_identity(GraphRunId("a:1"), 2, NodeId("b")) != task_identity(GraphRunId("a"), 1, NodeId("2:b"))


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ((GraphRunId("first"), 0, NodeId("a")), (GraphRunId("second"), 0, NodeId("a"))),
        ((GraphRunId("run"), 0, NodeId("a")), (GraphRunId("run"), 1, NodeId("a"))),
        ((GraphRunId("run"), 0, NodeId("a")), (GraphRunId("run"), 0, NodeId("b"))),
    ],
)
def test_each_committed_task_coordinate_changes_identity(
    left: tuple[GraphRunId, int, NodeId], right: tuple[GraphRunId, int, NodeId]
) -> None:
    assert task_identity(*left) != task_identity(*right)


def test_declaration_and_frontier_order_do_not_change_planned_batch() -> None:
    first = plan_tasks(
        compiled_graph("c", "a", "b", entries=("c", "a", "b")),
        snapshot(frontier=("b", "c", "a")),
        LIMITS,
    )
    second = plan_tasks(
        compiled_graph("b", "c", "a", entries=("a", "b", "c")),
        snapshot(frontier=("c", "a", "b")),
        LIMITS,
    )
    assert first == second


def test_graph_task_is_immutable() -> None:
    task = plan_tasks(compiled_graph("a"), snapshot(), LIMITS)[0]
    with pytest.raises(FrozenInstanceError):
        task.node_id = NodeId("other")  # type: ignore[misc]


def test_graph_task_has_no_retry_or_attempt_policy() -> None:
    assert {field.name for field in fields(GraphTask)} == {"task_id", "run_id", "superstep", "node_id"}


@pytest.mark.parametrize("status", [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED])
def test_nonrunning_snapshot_has_no_ready_tasks(status: ExecutionStatus) -> None:
    assert plan_tasks(compiled_graph("a"), snapshot(status=status, frontier=()), LIMITS) == ()


def test_suspended_snapshot_may_retain_recoverable_frontier() -> None:
    assert plan_tasks(compiled_graph("a"), snapshot(status=ExecutionStatus.SUSPENDED, frontier=("a",)), LIMITS) == ()


@pytest.mark.parametrize("execution_snapshot", [snapshot(definition_id="other"), snapshot(version=2)])
def test_snapshot_must_match_compiled_graph(execution_snapshot: object) -> None:
    with pytest.raises(SnapshotMismatchError):
        plan_tasks(compiled_graph("a"), execution_snapshot, LIMITS)  # type: ignore[arg-type]


def test_graph_mismatch_takes_precedence_over_foreign_frontier_nodes() -> None:
    with pytest.raises(SnapshotMismatchError):
        plan_tasks(compiled_graph("a"), snapshot(definition_id="other.graph", frontier=("foreign",)), LIMITS)


@pytest.mark.parametrize("status", list(ExecutionStatus))
def test_frontier_must_belong_to_compiled_graph_for_every_lifecycle(status: ExecutionStatus) -> None:
    with pytest.raises(InvalidExecutionSnapshotError, match="unknown nodes"):
        plan_tasks(compiled_graph("a"), snapshot(status=status, frontier=("unknown",)), LIMITS)


@pytest.mark.parametrize("status", list(ExecutionStatus))
def test_join_progress_must_belong_to_compiled_graph_for_every_lifecycle(status: ExecutionStatus) -> None:
    progress = JoinProgress((NodeId("a"), NodeId("b")), NodeId("c"), frozenset({NodeId("a")}))
    with pytest.raises(InvalidExecutionSnapshotError, match="unknown join"):
        plan_tasks(
            compiled_graph("a", "b", "c", entries=("a", "b", "c")),
            snapshot(status=status, frontier=("b",), join_progress=(progress,)),
            LIMITS,
        )


@pytest.mark.parametrize("limits", [ExecutionLimits(max_supersteps=0), ExecutionLimits(max_parallel_tasks=0)])
def test_invalid_limits_fail_closed(limits: ExecutionLimits) -> None:
    with pytest.raises(ExecutionLimitError):
        plan_tasks(compiled_graph("a"), snapshot(), limits)


@pytest.mark.parametrize("status", [ExecutionStatus.SUSPENDED, ExecutionStatus.COMPLETED, ExecutionStatus.FAILED])
def test_invalid_limits_fail_even_when_snapshot_has_no_tasks(status: ExecutionStatus) -> None:
    frontier = ("a",) if status is ExecutionStatus.SUSPENDED else ()
    with pytest.raises(ExecutionLimitError):
        plan_tasks(
            compiled_graph("a"),
            snapshot(status=status, frontier=frontier),
            ExecutionLimits(max_parallel_tasks=0),
        )


def test_superstep_limit_fails_closed_at_boundary() -> None:
    with pytest.raises(ExecutionLimitError, match="superstep"):
        plan_tasks(compiled_graph("a"), snapshot(superstep=3), ExecutionLimits(max_supersteps=3))


def test_last_allowed_superstep_is_plannable() -> None:
    assert plan_tasks(compiled_graph("a"), snapshot(superstep=2), ExecutionLimits(max_supersteps=3))


def test_parallel_limit_fails_closed_at_boundary() -> None:
    with pytest.raises(ExecutionLimitError, match="parallel"):
        plan_tasks(
            compiled_graph("a", "b", entries=("a", "b")),
            snapshot(frontier=("a", "b")),
            ExecutionLimits(max_parallel_tasks=1),
        )


def test_parallel_limit_allows_exactly_the_configured_batch_size() -> None:
    tasks = plan_tasks(
        compiled_graph("a", "b", entries=("a", "b")),
        snapshot(frontier=("a", "b")),
        ExecutionLimits(max_parallel_tasks=2),
    )
    assert tuple(task.node_id for task in tasks) == (NodeId("a"), NodeId("b"))


def test_valid_parent_linkage_does_not_change_parent_task_identity() -> None:
    without_parent = plan_tasks(compiled_graph("a"), snapshot(), LIMITS)
    with_parent = plan_tasks(compiled_graph("a"), snapshot(parent_run_id="parent"), LIMITS)
    assert with_parent == without_parent


def test_terminal_snapshot_is_not_rejected_only_for_reaching_step_limit() -> None:
    assert (
        plan_tasks(
            compiled_graph("a"),
            snapshot(status=ExecutionStatus.COMPLETED, superstep=10, frontier=()),
            ExecutionLimits(max_supersteps=10),
        )
        == ()
    )


def test_nested_graph_node_is_planned_as_one_parent_task() -> None:
    async def child_node(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child-step"), child_node),),
        (DirectEdge(NodeId("child-step"), END),),
        (NodeId("child-step"),),
    )
    parent = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(NodeId("nested"), child),),
            (DirectEdge(NodeId("nested"), END),),
            (NodeId("nested"),),
        )
    )
    assert plan_tasks(parent, snapshot(frontier=("nested",)), LIMITS)[0].node_id == NodeId("nested")


def test_planner_accepts_large_deterministic_frontier_at_exact_limit() -> None:
    node_ids = tuple(f"node-{index:04d}" for index in range(256))
    tasks = plan_tasks(
        compiled_graph(*reversed(node_ids), entries=tuple(reversed(node_ids))),
        snapshot(frontier=tuple(reversed(node_ids))),
        ExecutionLimits(max_parallel_tasks=len(node_ids)),
    )
    assert tuple(task.node_id for task in tasks) == tuple(NodeId(node_id) for node_id in node_ids)


def test_planner_does_not_invoke_node() -> None:
    calls = 0

    async def forbidden_node(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), forbidden_node),),
            (),
            (NodeId("a"),),
        )
    )
    plan_tasks(graph, snapshot(), LIMITS)
    assert calls == 0

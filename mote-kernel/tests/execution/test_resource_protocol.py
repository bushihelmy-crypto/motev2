import pytest

from mote_kernel.execution import (
    ExecutedFrontierBatch,
    ExecutedSuperstep,
    NestedTaskSuccess,
    PreparedFrontier,
    StepRequest,
    step_graph,
)
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import ResultCollectionError
from mote_kernel.execution.graph import (
    END,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeFailure,
    NodeId,
    NodeSuccess,
    RouteId,
    compile_graph,
)
from mote_kernel.execution.graph.command import SelectRoute
from mote_kernel.execution.result import TaskSuccess
from mote_kernel.execution.snapshot import GraphRunId as ExecutionRunId
from mote_kernel.parallel import (
    AcquireResources,
    ParallelSnapshot,
    ParticipantId,
    ResourceDefinition,
    ResourceId,
    ResourceLock,
    reduce_parallel,
)
from mote_kernel.state.graph_state import (
    CompleteGraphRun,
    FailGraphRun,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphTaskId,
    reduce_graph_run,
)
from mote_kernel.state.graph_state import GraphDefinitionId as StateDefinitionId
from mote_kernel.state.graph_state import GraphDefinitionVersion as StateDefinitionVersion

FILE = ResourceId("file")


def state() -> GraphRunState:
    return GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("resource.graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        0,
        (GraphNodeId("a"), GraphNodeId("b")),
    )


def test_resource_frontier_uses_prepare_execute_release_batches_before_settlement() -> None:
    calls: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        def execute(node_input: str) -> NodeSuccess[str]:
            calls.append(name)
            return NodeSuccess(f"{name}:{node_input}")

        return NodeDefinition(NodeId(name), execute, (FILE,))

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (node("a"), node("b")),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )

    prepared_a = step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared_a, PreparedFrontier)
    assert prepared_a.admission is not None
    assert calls == []

    admitted_a = reduce_graph_run(state(), prepared_a.admission.command)
    executed_a = step_graph(StepRequest(graph, admitted_a, "input"))
    assert isinstance(executed_a, ExecutedFrontierBatch)
    assert calls == ["a"]

    released_a = reduce_graph_run(admitted_a, executed_a.command)
    executed_b = step_graph(
        StepRequest(
            graph,
            released_a,
            "input",
            settled_results=executed_a.results,
        )
    )
    assert isinstance(executed_b, ExecutedSuperstep)
    assert calls == ["a", "b"]
    assert executed_b.command == CompleteGraphRun(0)
    assert tuple(result.task.node_id for result in executed_b.results) == (NodeId("a"), NodeId("b"))


def test_nonconflicting_resource_tasks_execute_in_one_committed_batch() -> None:
    database = ResourceId("database")

    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NodeDefinition(NodeId("b"), execute, (database,)),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(database, 20)),
        )
    )

    prepared = step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None

    admitted = reduce_graph_run(state(), prepared.admission.command)
    executed = step_graph(StepRequest(graph, admitted, "input"))
    assert isinstance(executed, ExecutedSuperstep)
    assert len(executed.results) == 2


def test_resource_and_resource_free_tasks_share_the_committed_execution_batch() -> None:
    calls: list[str] = []

    def execute(node_input: str) -> NodeSuccess[str]:
        calls.append(node_input)
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NodeDefinition(NodeId("b"), execute),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )

    prepared = step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert calls == []

    admitted = reduce_graph_run(state(), prepared.admission.command)
    executed = step_graph(StepRequest(graph, admitted, "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert calls == ["input", "input"]
    assert tuple(result.task.node_id for result in executed.results) == (NodeId("a"), NodeId("b"))


def test_settled_results_must_uniquely_belong_to_current_frontier() -> None:
    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute), NodeDefinition(NodeId("b"), execute)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    task = step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(task, ExecutedSuperstep)
    duplicate = TaskSuccess(task.results[0].task, "prior")

    with pytest.raises(ResultCollectionError, match="settled"):
        step_graph(StepRequest(graph, state(), "input", settled_results=(duplicate, duplicate)))


@pytest.mark.parametrize(
    "corrupt_task",
    [
        GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("other"), 0, NodeId("a")),
        GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("run"), 1, NodeId("a")),
        GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("run"), 0, NodeId("b")),
    ],
)
def test_settled_result_coordinates_fail_before_other_nodes_execute(corrupt_task: GraphTask) -> None:
    calls: list[str] = []

    def execute(node_input: str) -> NodeSuccess[str]:
        calls.append(node_input)
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute), NodeDefinition(NodeId("b"), execute)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    corrupted = TaskSuccess(corrupt_task, "prior")

    with pytest.raises(ResultCollectionError, match="exactly match"):
        step_graph(StepRequest(graph, state(), "input", settled_results=(corrupted,)))

    assert calls == []


def test_settled_result_values_must_match_committed_settled_task_state() -> None:
    graph_state = state()
    object.__setattr__(graph_state, "settled_tasks", (GraphTaskId("3:run:0:1:a"),))

    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute), NodeDefinition(NodeId("b"), execute)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )

    with pytest.raises(ResultCollectionError, match="exactly cover"):
        step_graph(StepRequest(graph, graph_state, "input"))

    object.__setattr__(graph_state, "settled_tasks", (GraphTaskId("unknown"),))
    unknown = TaskSuccess(
        GraphTask(TaskId("unknown"), ExecutionRunId("run"), 0, NodeId("a")),
        "prior",
    )
    with pytest.raises(ResultCollectionError):
        step_graph(StepRequest(graph, graph_state, "input", settled_results=(unknown,)))


def test_resource_and_nested_tasks_do_not_cross_execution_protocols() -> None:
    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child-step"), execute),),
        (DirectEdge(NodeId("child-step"), END),),
        (NodeId("child-step"),),
    )
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NestedGraphNodeDefinition(NodeId("b"), child),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )

    prepared = step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("a"),)
    assert tuple(run.parent_task.node_id for run in prepared.nested_runs) == (NodeId("b"),)

    admitted = reduce_graph_run(state(), prepared.admission.command)
    executed = step_graph(StepRequest(graph, admitted, "input"))

    assert isinstance(executed, ExecutedFrontierBatch)
    assert tuple(result.task.node_id for result in executed.results) == (NodeId("a"),)


def test_committed_parallel_snapshot_must_match_compiled_resource_order() -> None:
    database = ResourceId("database")

    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute, (FILE,)), NodeDefinition(NodeId("b"), execute)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(database, 20)),
        )
    )
    mismatched = state()
    object.__setattr__(mismatched, "parallel", ParallelSnapshot((ResourceLock(FILE),)))

    with pytest.raises(ResultCollectionError, match="resource order"):
        step_graph(StepRequest(graph, mismatched, "input"))


def test_parallel_snapshot_is_validated_without_pending_resource_tasks() -> None:
    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute), NodeDefinition(NodeId("b"), execute)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
        )
    )
    mismatched = state()
    object.__setattr__(mismatched, "parallel", ParallelSnapshot((ResourceLock(FILE),)))

    with pytest.raises(ResultCollectionError, match="resource order"):
        step_graph(StepRequest(graph, mismatched, "input"))


def test_parallel_snapshot_rejects_stale_acquisition_without_pending_resource_tasks() -> None:
    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), execute), NodeDefinition(NodeId("b"), execute)),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    stale = state()
    acquired = reduce_parallel(
        ParallelSnapshot((ResourceLock(FILE),)),
        AcquireResources(ParticipantId("stale"), (FILE,)),
    )
    object.__setattr__(stale, "parallel", acquired)

    with pytest.raises(ResultCollectionError, match="outside pending resource tasks"):
        step_graph(StepRequest(graph, stale, "input"))


def test_parallel_snapshot_rejects_acquisition_for_a_settled_resource_task() -> None:
    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NodeDefinition(NodeId("b"), execute, (FILE,)),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    task_a = GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("run"), 0, NodeId("a"))
    corrupted = state()
    acquisition = reduce_parallel(
        ParallelSnapshot((ResourceLock(FILE),)),
        AcquireResources(ParticipantId(task_a.task_id), (FILE,)),
    )
    object.__setattr__(corrupted, "parallel", acquisition)
    object.__setattr__(corrupted, "settled_tasks", (GraphTaskId(task_a.task_id),))

    with pytest.raises(ResultCollectionError, match="outside pending resource tasks"):
        step_graph(
            StepRequest(
                graph,
                corrupted,
                "input",
                settled_results=(TaskSuccess(task_a, "settled"),),
            )
        )


def test_settled_resource_then_nested_rejects_mismatched_parallel_before_child_preparation() -> None:
    database = ResourceId("database")

    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child-step"), execute),),
        (DirectEdge(NodeId("child-step"), END),),
        (NodeId("child-step"),),
    )
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NestedGraphNodeDefinition(NodeId("b"), child),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(database, 20)),
        )
    )
    task_a = GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("run"), 0, NodeId("a"))
    recovered = state()
    object.__setattr__(recovered, "parallel", ParallelSnapshot((ResourceLock(database), ResourceLock(FILE))))
    object.__setattr__(recovered, "settled_tasks", (GraphTaskId(task_a.task_id),))

    with pytest.raises(ResultCollectionError, match="resource order"):
        step_graph(
            StepRequest(
                graph,
                recovered,
                "input",
                settled_results=(TaskSuccess(task_a, "settled"),),
            )
        )


def test_settled_resource_then_nested_allows_matching_released_parallel_snapshot() -> None:
    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child-step"), execute),),
        (DirectEdge(NodeId("child-step"), END),),
        (NodeId("child-step"),),
    )
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NestedGraphNodeDefinition(NodeId("b"), child),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    task_a = GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("run"), 0, NodeId("a"))
    recovered = state()
    object.__setattr__(recovered, "parallel", ParallelSnapshot((ResourceLock(FILE),)))
    object.__setattr__(recovered, "settled_tasks", (GraphTaskId(task_a.task_id),))

    prepared = step_graph(
        StepRequest(
            graph,
            recovered,
            "input",
            settled_results=(TaskSuccess(task_a, "settled"),),
        )
    )

    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is None
    assert tuple(run.parent_task.node_id for run in prepared.nested_runs) == (NodeId("b"),)


def test_failed_resource_task_releases_lock_and_settles_failure() -> None:
    def fail(node_input: str) -> NodeFailure:
        return NodeFailure(f"failed: {node_input}")

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(NodeId("a"), fail, (FILE,)), NodeDefinition(NodeId("b"), fail, (FILE,))),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    prepared = step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    admitted = reduce_graph_run(state(), prepared.admission.command)

    first = step_graph(StepRequest(graph, admitted, "input"))
    assert isinstance(first, ExecutedFrontierBatch)
    released = reduce_graph_run(admitted, first.command)
    assert released.parallel is not None
    assert released.parallel.resources[0].owner is not None

    completed = step_graph(StepRequest(graph, released, "input", settled_results=first.results))
    assert isinstance(completed, ExecutedSuperstep)
    assert isinstance(completed.command, FailGraphRun)
    assert completed.command.failure == "failed: input"


def test_conditional_route_acquires_only_the_selected_target_resource() -> None:
    def choose(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input, SelectRoute(RouteId("left")))

    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), choose),
                NodeDefinition(NodeId("left"), execute, (FILE,)),
                NodeDefinition(NodeId("right"), execute, (FILE,)),
            ),
            (
                ConditionalEdge(NodeId("a"), RouteId("left"), NodeId("left")),
                ConditionalEdge(NodeId("a"), RouteId("right"), NodeId("right")),
                DirectEdge(NodeId("left"), END),
                DirectEdge(NodeId("right"), END),
            ),
            (NodeId("a"),),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("resource.graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        0,
        (GraphNodeId("a"),),
    )
    routed = step_graph(StepRequest(graph, initial, "input"))
    assert isinstance(routed, ExecutedSuperstep)
    next_state = reduce_graph_run(initial, routed.command)

    prepared = step_graph(StepRequest(graph, next_state, "input"))

    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("left"),)


def test_partially_held_multi_resource_task_does_not_execute_before_full_admission() -> None:
    database = ResourceId("database")
    calls: list[str] = []

    def execute(node_input: str) -> NodeSuccess[str]:
        calls.append(node_input)
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (database,)),
                NodeDefinition(NodeId("b"), execute, (FILE, database)),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(database, 20)),
        )
    )

    prepared = step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("a"),)
    assert tuple(task.node_id for task in prepared.admission.waiting) == (NodeId("b"),)
    assert calls == []

    admitted = reduce_graph_run(state(), prepared.admission.command)
    first = step_graph(StepRequest(graph, admitted, "input"))

    assert isinstance(first, ExecutedFrontierBatch)
    assert tuple(result.task.node_id for result in first.results) == (NodeId("a"),)
    assert calls == ["input"]


def test_resource_batch_then_nested_graph_can_settle_one_frontier() -> None:
    def execute(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    child = GraphDefinition[str, str](
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(NodeId("child-step"), execute),),
        (DirectEdge(NodeId("child-step"), END),),
        (NodeId("child-step"),),
    )
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute, (FILE,)),
                NestedGraphNodeDefinition(NodeId("b"), child),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state()
    prepared = step_graph(StepRequest(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert len(prepared.nested_runs) == 1
    nested_run = prepared.nested_runs[0]

    child_state = reduce_graph_run(None, nested_run.command)
    child_execution = step_graph(StepRequest(nested_run.graph, child_state, "input"))
    assert isinstance(child_execution, ExecutedSuperstep)
    child_result = child_execution.results[0]
    assert isinstance(child_result, TaskSuccess)
    completed_child = reduce_graph_run(child_state, child_execution.command)

    admitted = reduce_graph_run(initial, prepared.admission.command)
    batch = step_graph(StepRequest(graph, admitted, "input"))
    assert isinstance(batch, ExecutedFrontierBatch)
    released = reduce_graph_run(admitted, batch.command)

    completed = step_graph(
        StepRequest(
            graph,
            released,
            "input",
            nested_results=(NestedTaskSuccess(nested_run.parent_task.task_id, completed_child, child_result.output),),
            settled_results=batch.results,
        )
    )

    assert isinstance(completed, ExecutedSuperstep)
    assert completed.command == CompleteGraphRun(0)
    assert tuple(result.task.node_id for result in completed.results) == (NodeId("a"), NodeId("b"))

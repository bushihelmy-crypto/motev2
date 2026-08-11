import asyncio

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
pytestmark = pytest.mark.asyncio


def state() -> GraphRunState:
    return GraphRunState(
        GraphRunId("run"),
        StateDefinitionId("resource.graph"),
        StateDefinitionVersion(1),
        GraphRunStatus.RUNNING,
        0,
        (GraphNodeId("a"), GraphNodeId("b")),
    )


async def test_resource_frontier_uses_prepare_execute_release_batches_before_settlement() -> None:
    calls: list[str] = []

    def node(name: str) -> NodeDefinition[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
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

    prepared_a = await step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared_a, PreparedFrontier)
    assert prepared_a.admission is not None
    assert calls == []

    admitted_a = reduce_graph_run(state(), prepared_a.admission.command)
    executed_a = await step_graph(StepRequest(graph, admitted_a, "input"))
    assert isinstance(executed_a, ExecutedFrontierBatch)
    assert calls == ["a"]

    released_a = reduce_graph_run(admitted_a, executed_a.command)
    executed_b = await step_graph(
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


async def test_nonconflicting_resource_tasks_execute_in_one_committed_batch() -> None:
    database = ResourceId("database")
    both_started = asyncio.Barrier(2)

    async def execute(node_input: str) -> NodeSuccess[str]:
        await asyncio.wait_for(both_started.wait(), timeout=2)
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

    prepared = await step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None

    admitted = reduce_graph_run(state(), prepared.admission.command)
    executed = await step_graph(StepRequest(graph, admitted, "input"))
    assert isinstance(executed, ExecutedSuperstep)
    assert len(executed.results) == 2


async def test_cancelling_partially_completed_resource_batch_preserves_recoverable_state_for_retry() -> None:
    database = ResourceId("database")
    completed_a = asyncio.Event()
    started_b = asyncio.Event()
    allow_b_completion = asyncio.Event()
    cancelled: list[str] = []
    calls: list[str] = []

    async def execute_a(node_input: str) -> NodeSuccess[str]:
        calls.append("a")
        completed_a.set()
        return NodeSuccess(f"a:{node_input}")

    async def execute_b(node_input: str) -> NodeSuccess[str]:
        calls.append("b")
        started_b.set()
        try:
            await allow_b_completion.wait()
        except asyncio.CancelledError:
            cancelled.append("b")
            raise
        return NodeSuccess(f"b:{node_input}")

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute_a, (FILE,)),
                NodeDefinition(NodeId("b"), execute_b, (database,)),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10), ResourceDefinition(database, 20)),
        )
    )
    initial = state()
    prepared = await step_graph(StepRequest(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    admitted = reduce_graph_run(initial, prepared.admission.command)
    assert admitted.parallel is not None
    committed_resources = tuple(
        (resource.resource_id, resource.owner, resource.waiters) for resource in admitted.parallel.resources
    )
    committed_acquisitions = tuple(
        (
            acquisition.participant_id,
            acquisition.required,
            acquisition.acquired,
            acquisition.waiting_for,
        )
        for acquisition in admitted.parallel.acquisitions
    )

    execution = asyncio.create_task(step_graph(StepRequest(graph, admitted, "input")))
    async with asyncio.timeout(2):
        await asyncio.gather(completed_a.wait(), started_b.wait())
    execution.cancel()

    with pytest.raises(asyncio.CancelledError):
        await execution

    assert cancelled == ["b"]
    assert admitted.parallel is not None
    assert (
        tuple((resource.resource_id, resource.owner, resource.waiters) for resource in admitted.parallel.resources)
        == committed_resources
    )
    assert (
        tuple(
            (
                acquisition.participant_id,
                acquisition.required,
                acquisition.acquired,
                acquisition.waiting_for,
            )
            for acquisition in admitted.parallel.acquisitions
        )
        == committed_acquisitions
    )
    assert all(resource.owner is not None for resource in admitted.parallel.resources)
    assert admitted.settled_tasks == ()

    allow_b_completion.set()
    retried = await step_graph(StepRequest(graph, admitted, "input"))

    assert isinstance(retried, ExecutedSuperstep)
    assert retried.command == CompleteGraphRun(0)
    assert calls.count("a") == 2
    assert calls.count("b") == 2
    assert tuple(result.output for result in retried.results if isinstance(result, TaskSuccess)) == (
        "a:input",
        "b:input",
    )


async def test_resource_wait_queue_recovers_from_node_exception_by_replaying_committed_state() -> None:
    attempts_a = 0
    calls_b = 0

    async def execute_a(node_input: str) -> NodeSuccess[str]:
        nonlocal attempts_a
        attempts_a += 1
        if attempts_a == 1:
            raise RuntimeError("a failed before settlement")
        return NodeSuccess(f"a:{node_input}")

    async def execute_b(node_input: str) -> NodeSuccess[str]:
        nonlocal calls_b
        calls_b += 1
        return NodeSuccess(f"b:{node_input}")

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(NodeId("a"), execute_a, (FILE,)),
                NodeDefinition(NodeId("b"), execute_b, (FILE,)),
            ),
            (DirectEdge(NodeId("a"), END), DirectEdge(NodeId("b"), END)),
            (NodeId("a"), NodeId("b")),
            (ResourceDefinition(FILE, 10),),
        )
    )
    initial = state()
    prepared = await step_graph(StepRequest(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    task_a = prepared.admission.admitted[0]
    task_b = prepared.admission.waiting[0]
    admitted = reduce_graph_run(initial, prepared.admission.command)

    with pytest.raises(RuntimeError, match="before settlement"):
        await step_graph(StepRequest(graph, admitted, "input"))

    assert admitted.parallel is not None
    assert admitted.parallel.resources == (
        ResourceLock(FILE, ParticipantId(task_a.task_id), (ParticipantId(task_b.task_id),)),
    )
    assert admitted.settled_tasks == ()
    assert attempts_a == 1
    assert calls_b == 0

    replayed = await step_graph(StepRequest(graph, admitted, "input"))
    assert isinstance(replayed, ExecutedFrontierBatch)
    released = reduce_graph_run(admitted, replayed.command)
    assert released.parallel is not None
    assert released.parallel.resources == (ResourceLock(FILE, ParticipantId(task_b.task_id)),)
    assert released.settled_tasks == (GraphTaskId(task_a.task_id),)

    completed = await step_graph(StepRequest(graph, released, "input", settled_results=replayed.results))

    assert isinstance(completed, ExecutedSuperstep)
    assert completed.command == CompleteGraphRun(0)
    assert attempts_a == 2
    assert calls_b == 1
    assert tuple(result.output for result in completed.results if isinstance(result, TaskSuccess)) == (
        "a:input",
        "b:input",
    )


async def test_resource_and_resource_free_tasks_share_the_committed_execution_batch() -> None:
    calls: list[str] = []

    async def execute(node_input: str) -> NodeSuccess[str]:
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

    prepared = await step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert calls == []

    admitted = reduce_graph_run(state(), prepared.admission.command)
    executed = await step_graph(StepRequest(graph, admitted, "input"))

    assert isinstance(executed, ExecutedSuperstep)
    assert calls == ["input", "input"]
    assert tuple(result.task.node_id for result in executed.results) == (NodeId("a"), NodeId("b"))


async def test_settled_results_must_uniquely_belong_to_current_frontier() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
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
    task = await step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(task, ExecutedSuperstep)
    duplicate = TaskSuccess(task.results[0].task, "prior")

    with pytest.raises(ResultCollectionError, match="settled"):
        await step_graph(StepRequest(graph, state(), "input", settled_results=(duplicate, duplicate)))


@pytest.mark.parametrize(
    "corrupt_task",
    [
        GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("other"), 0, NodeId("a")),
        GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("run"), 1, NodeId("a")),
        GraphTask(TaskId("3:run:0:1:a"), ExecutionRunId("run"), 0, NodeId("b")),
    ],
)
async def test_settled_result_coordinates_fail_before_other_nodes_execute(corrupt_task: GraphTask) -> None:
    calls: list[str] = []

    async def execute(node_input: str) -> NodeSuccess[str]:
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
        await step_graph(StepRequest(graph, state(), "input", settled_results=(corrupted,)))

    assert calls == []


async def test_settled_result_values_must_match_committed_settled_task_state() -> None:
    graph_state = state()
    object.__setattr__(graph_state, "settled_tasks", (GraphTaskId("3:run:0:1:a"),))

    async def execute(node_input: str) -> NodeSuccess[str]:
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
        await step_graph(StepRequest(graph, graph_state, "input"))

    object.__setattr__(graph_state, "settled_tasks", (GraphTaskId("unknown"),))
    unknown = TaskSuccess(
        GraphTask(TaskId("unknown"), ExecutionRunId("run"), 0, NodeId("a")),
        "prior",
    )
    with pytest.raises(ResultCollectionError):
        await step_graph(StepRequest(graph, graph_state, "input", settled_results=(unknown,)))


async def test_resource_and_nested_tasks_do_not_cross_execution_protocols() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
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

    prepared = await step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("a"),)
    assert tuple(run.parent_task.node_id for run in prepared.nested_runs) == (NodeId("b"),)

    admitted = reduce_graph_run(state(), prepared.admission.command)
    executed = await step_graph(StepRequest(graph, admitted, "input"))

    assert isinstance(executed, ExecutedFrontierBatch)
    assert tuple(result.task.node_id for result in executed.results) == (NodeId("a"),)


async def test_committed_parallel_snapshot_must_match_compiled_resource_order() -> None:
    database = ResourceId("database")

    async def execute(node_input: str) -> NodeSuccess[str]:
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
        await step_graph(StepRequest(graph, mismatched, "input"))


async def test_parallel_snapshot_is_validated_without_pending_resource_tasks() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
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
        await step_graph(StepRequest(graph, mismatched, "input"))


async def test_parallel_snapshot_rejects_stale_acquisition_without_pending_resource_tasks() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
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
        await step_graph(StepRequest(graph, stale, "input"))


async def test_parallel_snapshot_rejects_acquisition_for_a_settled_resource_task() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
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
        await step_graph(
            StepRequest(
                graph,
                corrupted,
                "input",
                settled_results=(TaskSuccess(task_a, "settled"),),
            )
        )


async def test_settled_resource_then_nested_rejects_mismatched_parallel_before_child_preparation() -> None:
    database = ResourceId("database")

    async def execute(node_input: str) -> NodeSuccess[str]:
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
        await step_graph(
            StepRequest(
                graph,
                recovered,
                "input",
                settled_results=(TaskSuccess(task_a, "settled"),),
            )
        )


async def test_settled_resource_then_nested_allows_matching_released_parallel_snapshot() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
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

    prepared = await step_graph(
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


async def test_failed_resource_task_releases_lock_and_settles_failure() -> None:
    async def fail(node_input: str) -> NodeFailure:
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
    prepared = await step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    admitted = reduce_graph_run(state(), prepared.admission.command)

    first = await step_graph(StepRequest(graph, admitted, "input"))
    assert isinstance(first, ExecutedFrontierBatch)
    released = reduce_graph_run(admitted, first.command)
    assert released.parallel is not None
    assert released.parallel.resources[0].owner is not None

    completed = await step_graph(StepRequest(graph, released, "input", settled_results=first.results))
    assert isinstance(completed, ExecutedSuperstep)
    assert isinstance(completed.command, FailGraphRun)
    assert completed.command.failure == "failed: input"


async def test_conditional_route_acquires_only_the_selected_target_resource() -> None:
    async def choose(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input, SelectRoute(RouteId("left")))

    async def execute(node_input: str) -> NodeSuccess[str]:
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
    routed = await step_graph(StepRequest(graph, initial, "input"))
    assert isinstance(routed, ExecutedSuperstep)
    next_state = reduce_graph_run(initial, routed.command)

    prepared = await step_graph(StepRequest(graph, next_state, "input"))

    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("left"),)


async def test_partially_held_multi_resource_task_does_not_execute_before_full_admission() -> None:
    database = ResourceId("database")
    calls: list[str] = []

    async def execute(node_input: str) -> NodeSuccess[str]:
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

    prepared = await step_graph(StepRequest(graph, state(), "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert tuple(task.node_id for task in prepared.admission.admitted) == (NodeId("a"),)
    assert tuple(task.node_id for task in prepared.admission.waiting) == (NodeId("b"),)
    assert calls == []

    admitted = reduce_graph_run(state(), prepared.admission.command)
    first = await step_graph(StepRequest(graph, admitted, "input"))

    assert isinstance(first, ExecutedFrontierBatch)
    assert tuple(result.task.node_id for result in first.results) == (NodeId("a"),)
    assert calls == ["input"]


async def test_resource_batch_then_nested_graph_can_settle_one_frontier() -> None:
    async def execute(node_input: str) -> NodeSuccess[str]:
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
    prepared = await step_graph(StepRequest(graph, initial, "input"))
    assert isinstance(prepared, PreparedFrontier)
    assert prepared.admission is not None
    assert len(prepared.nested_runs) == 1
    nested_run = prepared.nested_runs[0]

    child_state = reduce_graph_run(None, nested_run.command)
    child_execution = await step_graph(StepRequest(nested_run.graph, child_state, "input"))
    assert isinstance(child_execution, ExecutedSuperstep)
    child_result = child_execution.results[0]
    assert isinstance(child_result, TaskSuccess)
    completed_child = reduce_graph_run(child_state, child_execution.command)

    admitted = reduce_graph_run(initial, prepared.admission.command)
    batch = await step_graph(StepRequest(graph, admitted, "input"))
    assert isinstance(batch, ExecutedFrontierBatch)
    released = reduce_graph_run(admitted, batch.command)

    completed = await step_graph(
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

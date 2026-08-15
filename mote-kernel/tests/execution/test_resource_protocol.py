import asyncio
from dataclasses import replace

import pytest

from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.errors import InvalidExecutionSnapshotError, ResultCollectionError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph import (
    END,
    CompiledGraph,
    ConditionalEdge,
    DirectEdge,
    GraphDefinition,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
    NestedGraphNodeDefinition,
    Node,
    NodeDefinition,
    NodeFailure,
    NodeInterrupt,
    NodeSuccess,
    ResumeInputBinding,
    SelectGraphRoute,
    compile_graph,
)
from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeRequest,
    StepRequest,
)
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.execution.result import (
    ActiveChild,
    CompletedChild,
    ExecutableFrontier,
    MissingChild,
    ReadyToResolve,
    StartMissingChildren,
    WaitingForChildren,
)
from mote_kernel.state.graph_state import (
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FailedGraphNode,
    FenceGraphExecution,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphInterruptPayload,
    GraphResumeInputCodecId,
    GraphRunId,
    GraphRunState,
    GraphStateTransitionError,
    InterruptedGraphNode,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    SettleGraphNode,
    SucceededGraphNode,
    SucceededGraphNodeOutcome,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


def resource_graph(
    node: Node[str, str],
    *,
    entries: tuple[str, ...] = ("a", "b"),
    resource: ResourceId | None = None,
) -> CompiledGraph[str, str]:
    effective_resource = ResourceId("file") if resource is None else resource
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            tuple(NodeDefinition(GraphNodeId(name), node, (effective_resource,)) for name in entries),
            tuple(DirectEdge(GraphNodeId(name), END) for name in entries),
            tuple(GraphNodeId(name) for name in entries),
            (ResourceDefinition(effective_resource, 0),),
        )
    )


async def prepare_claim(
    graph: CompiledGraph[str, str],
    state: GraphRunState,
    limits: ExecutionLimits | None = None,
) -> tuple[GraphExecutor[str, str], GraphRunState, GraphExecutionSession[str, str]]:
    effective_limits = ExecutionLimits() if limits is None else limits
    executor = GraphExecutor(graph)
    request = StepRequest(state, "input", ExecutionRequestAttemptId("request"), (), effective_limits)
    prepared = await executor.prepare(request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), (), effective_limits),
    )
    return executor, claimed, session


async def drain_session(
    state: GraphRunState,
    session: GraphExecutionSession[str, str],
) -> GraphRunState:
    current = state
    try:
        while current.execution is not None:
            result = await session.next(current)
            current = reduce_graph_run(current, result.command)
    finally:
        await session.aclose()
    return current


def nested_resource_graph() -> CompiledGraph[str, str]:
    async def echo(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    child = GraphDefinition(
        GraphDefinitionId("resource.child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    resource = ResourceId("file")
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.nested"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("nested"), child),
                NodeDefinition(GraphNodeId("resource"), echo, (resource,)),
            ),
            (DirectEdge(GraphNodeId("nested"), END), DirectEdge(GraphNodeId("resource"), END)),
            (GraphNodeId("nested"), GraphNodeId("resource")),
            (ResourceDefinition(resource, 0),),
        )
    )


async def test_claim_admits_all_resource_nodes_once() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = resource_graph(node)
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(StepRequest(initial, "input", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    assert prepared.claim.command.resources is not None
    assert tuple(item.node_id for item in prepared.claim.command.resources.acquisitions) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
    )


async def test_release_and_waiter_progress_are_authoritative_before_next_selection() -> None:
    order: list[str] = []
    gate = asyncio.Event()

    async def node(value: str) -> NodeSuccess[str]:
        order.append(value)
        if value == "input":
            await gate.wait()
        return NodeSuccess(value)

    graph = resource_graph(node)
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(StepRequest(initial, "input", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), ()),
    )
    try:
        first_task = asyncio.create_task(session.next(claimed))
        await asyncio.sleep(0)
        gate.set()
        first = await first_task
        after = reduce_graph_run(claimed, first.command)
        assert after.resources is not None and after.resources.acquisitions[0].admitted
        second = await session.next(after)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_resource_free_and_resource_admitted_nodes_share_session_scheduler() -> None:
    calls: list[str] = []

    async def free(value: str) -> NodeSuccess[str]:
        calls.append("free")
        return NodeSuccess(value)

    resource = ResourceId("file")
    graph: CompiledGraph[str, str] = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.graph"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("free"), free),
                NodeDefinition(GraphNodeId("locked"), free, (resource,)),
            ),
            (DirectEdge(GraphNodeId("free"), END), DirectEdge(GraphNodeId("locked"), END)),
            (GraphNodeId("free"), GraphNodeId("locked")),
            (ResourceDefinition(resource, 0),),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(StepRequest(initial, "input", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(
        prepared.claim, StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), ())
    )
    current = claimed
    try:
        while current.execution is not None:
            result = await session.next(current)
            current = reduce_graph_run(current, result.command)
    finally:
        await session.aclose()
    assert len(calls) == 2


async def test_ordinary_error_stops_unstarted_waiters_and_fence_clears_remaining_claim() -> None:
    started: list[str] = []

    async def fail(value: str) -> NodeSuccess[str]:
        started.append(value)
        raise RuntimeError("failed")

    graph = resource_graph(fail)
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(
        StepRequest(initial, "input", ExecutionRequestAttemptId("request"), (), ExecutionLimits(max_parallel_tasks=1))
    )
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), (), ExecutionLimits(max_parallel_tasks=1)),
    )
    with pytest.raises(RuntimeError):
        await session.next(claimed)
    await session.aclose()
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    assert fenced.execution is fenced.resources is None
    assert len(started) == 1


async def test_resource_session_close_is_quiescent_before_fence() -> None:
    started = asyncio.Event()

    async def node(value: str) -> NodeSuccess[str]:
        started.set()
        await asyncio.sleep(10)
        return NodeSuccess(value)

    graph = resource_graph(node, entries=("a",))
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, claimed, session = await prepare_claim(graph, initial)
    task = asyncio.create_task(session.next(claimed))
    await started.wait()
    await session.aclose()
    assert session.quiescent
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    assert fenced.execution is None
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_three_conflicting_resource_nodes_are_released_and_selected_fifo() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = resource_graph(node, entries=("a", "b", "c"))
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, claimed, session = await prepare_claim(graph, initial)
    current = claimed
    order: list[GraphNodeId] = []
    try:
        while current.execution is not None:
            result = await session.next(current)
            order.append(result.result.task.node_id)
            current = reduce_graph_run(current, result.command)
    finally:
        await session.aclose()
    assert order == [GraphNodeId("a"), GraphNodeId("b"), GraphNodeId("c")]


async def test_partial_multi_resource_waiter_becomes_admitted_after_prefix_owner_settles() -> None:
    file_id = ResourceId("file")
    network_id = ResourceId("network")

    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.multi"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), node, (network_id,)),
                NodeDefinition(GraphNodeId("b"), node, (file_id, network_id)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(file_id, 0), ResourceDefinition(network_id, 1)),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, claimed, session = await prepare_claim(graph, initial)
    assert claimed.resources is not None
    waiting = next(item for item in claimed.resources.acquisitions if item.node_id == GraphNodeId("b"))
    assert waiting.acquired == (file_id,) and waiting.waiting_for == network_id
    try:
        first = await session.next(claimed)
        after = reduce_graph_run(claimed, first.command)
        assert after.resources is not None
        admitted = next(item for item in after.resources.acquisitions if item.node_id == GraphNodeId("b"))
        assert admitted.admitted
        second = await session.next(after)
        assert second.result.task.node_id == GraphNodeId("b")
    finally:
        await session.aclose()


async def test_resource_free_activation_has_no_fake_acquisition() -> None:
    resource = ResourceId("file")

    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.mixed"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("free"), node),
                NodeDefinition(GraphNodeId("locked"), node, (resource,)),
            ),
            (DirectEdge(GraphNodeId("free"), END), DirectEdge(GraphNodeId("locked"), END)),
            (GraphNodeId("free"), GraphNodeId("locked")),
            (ResourceDefinition(resource, 0),),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(StepRequest(initial, "input", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    resources = prepared.claim.command.resources
    assert resources is not None
    assert tuple(item.node_id for item in resources.acquisitions) == (GraphNodeId("locked"),)


async def test_nonconflicting_resource_nodes_run_concurrently_in_the_same_scheduler() -> None:
    file_id = ResourceId("file")
    network_id = ResourceId("network")
    barrier = asyncio.Barrier(2)
    started: list[str] = []

    def node(name: str) -> Node[str, str]:
        async def execute(node_input: str) -> NodeSuccess[str]:
            started.append(name)
            await asyncio.wait_for(barrier.wait(), timeout=1)
            return NodeSuccess(node_input)

        return execute

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.concurrent"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), node("a"), (file_id,)),
                NodeDefinition(GraphNodeId("b"), node("b"), (network_id,)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(file_id, 0), ResourceDefinition(network_id, 1)),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, current, session = await prepare_claim(graph, initial)
    try:
        first = await session.next(current)
        current = reduce_graph_run(current, first.command)
        second = await session.next(current)
        current = reduce_graph_run(current, second.command)
    finally:
        await session.aclose()
    assert sorted(started) == ["a", "b"]
    assert current.execution is current.resources is None


async def test_typed_resource_failure_releases_and_admits_its_waiter() -> None:
    resource = ResourceId("file")

    async def fail(_node_input: str) -> NodeFailure:
        return NodeFailure(GraphFailure("a failed"))

    async def succeed(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.failure"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), fail, (resource,)),
                NodeDefinition(GraphNodeId("b"), succeed, (resource,)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(resource, 0),),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, claimed, session = await prepare_claim(graph, initial)
    try:
        first = await session.next(claimed)
        assert first.result.task.node_id == GraphNodeId("a")
        after_failure = reduce_graph_run(claimed, first.command)
        assert after_failure.resources is not None
        waiter = after_failure.resources.acquisitions[0]
        assert waiter.node_id == GraphNodeId("b") and waiter.admitted
        second = await session.next(after_failure)
        settled = reduce_graph_run(after_failure, second.command)
    finally:
        await session.aclose()
    assert isinstance(settled.frontier.nodes[0].settlement, FailedGraphNode)
    assert isinstance(settled.frontier.nodes[1].settlement, SucceededGraphNode)
    assert settled.execution is settled.resources is None


async def test_conditional_frontier_admits_only_the_selected_resource_target() -> None:
    resource = ResourceId("file")

    async def select_left(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input, SelectGraphRoute(GraphRouteId("left")))

    async def node(node_input: str) -> NodeSuccess[str]:
        return NodeSuccess(node_input)

    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.conditional"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("start"), select_left),
                NodeDefinition(GraphNodeId("left"), node, (resource,)),
                NodeDefinition(GraphNodeId("right"), node, (resource,)),
            ),
            (
                ConditionalEdge(GraphNodeId("start"), GraphRouteId("left"), GraphNodeId("left")),
                ConditionalEdge(GraphNodeId("start"), GraphRouteId("right"), GraphNodeId("right")),
                DirectEdge(GraphNodeId("left"), END),
                DirectEdge(GraphNodeId("right"), END),
            ),
            (GraphNodeId("start"),),
            (ResourceDefinition(resource, 0),),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(StepRequest(initial, "input", ExecutionRequestAttemptId("start-request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    assert prepared.claim.command.resources is None
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", ExecutionRequestAttemptId("start-request"), ()),
    )
    try:
        completed = await session.next(claimed)
        settled = reduce_graph_run(claimed, completed.command)
    finally:
        await session.aclose()
    ready = await executor.prepare(StepRequest(settled, "input", ExecutionRequestAttemptId("resolve-request"), ()))
    assert isinstance(ready, ReadyToResolve)
    selected = reduce_graph_run(settled, ready.command)
    next_claim = await executor.prepare(
        StepRequest(selected, "input", ExecutionRequestAttemptId("selected-request"), ())
    )
    assert isinstance(next_claim, ExecutableFrontier)
    resources = next_claim.claim.command.resources
    assert resources is not None
    assert tuple(item.node_id for item in resources.acquisitions) == (GraphNodeId("left"),)


def completed_child_from(action: StartMissingChildren[str, str]) -> GraphRunState:
    child = reduce_graph_run(None, action.children[0].command)
    claimed = reduce_graph_run(
        child,
        ClaimGraphExecution(child.revision, GraphExecutionAttemptId("child-attempt"), None),
    )
    assert claimed.execution is not None
    settled = reduce_graph_run(
        claimed,
        SettleGraphNode(
            claimed.revision,
            claimed.execution.token,
            SucceededGraphNodeOutcome(GraphNodeId("child"), ContinueGraphRouting()),
        ),
    )
    return reduce_graph_run(settled, CompleteGraphFrontier(settled.revision))


async def test_missing_child_precedes_resource_admission() -> None:
    graph = nested_resource_graph()
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))

    missing = await executor.prepare(
        StepRequest(parent, "input", ExecutionRequestAttemptId("missing-request"), (MissingChild(activation),))
    )

    assert isinstance(missing, WaitingForChildren)
    assert isinstance(missing.action, StartMissingChildren)
    assert parent.execution is parent.resources is None
    child = completed_child_from(missing.action)
    projection = CompletedChild(activation, child, "child-output", ContinueGraphRouting())
    prepared = await executor.prepare(
        StepRequest(parent, "input", ExecutionRequestAttemptId("claim-request"), (projection,))
    )
    assert isinstance(prepared, ExecutableFrontier)
    resources = prepared.claim.command.resources
    assert resources is not None
    assert tuple(item.node_id for item in resources.acquisitions) == (GraphNodeId("resource"),)


async def test_active_child_blocks_resource_admission() -> None:
    graph = nested_resource_graph()
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(
        StepRequest(parent, "input", ExecutionRequestAttemptId("missing-request"), (MissingChild(activation),))
    )
    assert isinstance(missing, WaitingForChildren)
    assert isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)

    waiting = await executor.prepare(
        StepRequest(parent, "input", ExecutionRequestAttemptId("active-request"), (ActiveChild(activation, child),))
    )

    assert isinstance(waiting, WaitingForChildren)
    assert waiting.action.children == (ActiveChild(activation, child),)
    assert parent.execution is parent.resources is None


async def test_resource_and_completed_nested_node_share_one_settlement_session() -> None:
    graph = nested_resource_graph()
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(
        StepRequest(parent, "input", ExecutionRequestAttemptId("missing-request"), (MissingChild(activation),))
    )
    assert isinstance(missing, WaitingForChildren)
    assert isinstance(missing.action, StartMissingChildren)
    child = completed_child_from(missing.action)
    projection = CompletedChild(activation, child, "child-output", ContinueGraphRouting())
    request_id = ExecutionRequestAttemptId("claim-request")
    prepared = await executor.prepare(StepRequest(parent, "input", request_id, (projection,)))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(parent, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", request_id, (projection,)),
    )

    settled = await drain_session(claimed, session)

    assert all(isinstance(node.settlement, SucceededGraphNode) for node in settled.frontier.nodes)
    assert settled.execution is settled.resources is None


def two_resource_guard_graph() -> CompiledGraph[str, str]:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    file_id = ResourceId("file")
    database_id = ResourceId("database")
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.guards"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), node, (file_id,)),),
            (DirectEdge(GraphNodeId("a"), END),),
            (GraphNodeId("a"),),
            (ResourceDefinition(file_id, 0), ResourceDefinition(database_id, 1)),
        )
    )


async def claimed_guard_state() -> tuple[GraphExecutor[str, str], GraphRunState]:
    graph = two_resource_guard_graph()
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(StepRequest(initial, "input", ExecutionRequestAttemptId("guard-request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    return executor, reduce_graph_run(initial, prepared.claim.command)


async def test_compiled_resource_requirement_drift_fails_before_scheduling() -> None:
    executor, claimed = await claimed_guard_state()
    file_id = ResourceId("file")
    database_id = ResourceId("database")
    drifted = replace(
        claimed,
        resources=ResourceSnapshot(
            (ResourceLock(file_id), ResourceLock(database_id, GraphNodeId("a"))),
            (ResourceAcquisition(GraphNodeId("a"), (database_id,), (database_id,)),),
        ),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="exactly match"):
        await executor.prepare(StepRequest(drifted, "input", ExecutionRequestAttemptId("drift-request"), ()))


@pytest.mark.parametrize("case", ["wrong-order", "stale-participant"])
async def test_committed_resource_snapshot_rejects_each_authority_mismatch(case: str) -> None:
    executor, claimed = await claimed_guard_state()
    file_id = ResourceId("file")
    database_id = ResourceId("database")
    if case == "wrong-order":
        resources = ResourceSnapshot(
            (ResourceLock(database_id), ResourceLock(file_id, GraphNodeId("a"))),
            (ResourceAcquisition(GraphNodeId("a"), (file_id,), (file_id,)),),
        )
        error: type[Exception] = InvalidExecutionSnapshotError
    else:
        resources = ResourceSnapshot(
            (ResourceLock(file_id, GraphNodeId("stale")), ResourceLock(database_id)),
            (ResourceAcquisition(GraphNodeId("stale"), (file_id,), (file_id,)),),
        )
        error = GraphStateTransitionError

    with pytest.raises(error):
        await executor.prepare(
            StepRequest(
                replace(claimed, resources=resources),
                "input",
                ExecutionRequestAttemptId("invalid-resource-request"),
                (),
            )
        )


async def test_competing_resource_claims_have_one_durable_winner() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = resource_graph(node, entries=("a",))
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    request = StepRequest(initial, "input", ExecutionRequestAttemptId("request"), ())
    first = await executor.prepare(request)
    second = await executor.prepare(request)
    assert isinstance(first, ExecutableFrontier)
    assert isinstance(second, ExecutableFrontier)
    assert first.claim.command.attempt_id != second.claim.command.attempt_id

    claimed = reduce_graph_run(initial, first.claim.command)
    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(claimed, second.claim.command)
    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(
            second.claim,
            StepRequest(claimed, "input", ExecutionRequestAttemptId("request"), ()),
        )
    assert not second.claim.consumed


async def test_claimed_resource_session_revalidates_exact_participants() -> None:
    resource = ResourceId("file")

    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.participants"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), node, (resource,)),
                NodeDefinition(GraphNodeId("b"), node),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(resource, 0),),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    prepared = await executor.prepare(StepRequest(initial, "input", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    forged = replace(
        claimed,
        resources=ResourceSnapshot(
            (ResourceLock(resource, GraphNodeId("b")),),
            (ResourceAcquisition(GraphNodeId("b"), (resource,), (resource,)),),
        ),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="exactly match"):
        await executor.execute(
            prepared.claim,
            StepRequest(forged, "input", ExecutionRequestAttemptId("request"), ()),
        )
    assert not prepared.claim.consumed


async def test_later_waiter_error_preserves_earlier_authoritative_settlement() -> None:
    async def succeed(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    async def explode(value: str) -> NodeSuccess[str]:
        raise RuntimeError(f"later:{value}")

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.later-error"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), succeed, (ResourceId("file"),)),
                NodeDefinition(GraphNodeId("b"), explode, (ResourceId("file"),)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(ResourceId("file"), 0),),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, claimed, session = await prepare_claim(graph, initial)
    first = await session.next(claimed)
    after_first = reduce_graph_run(claimed, first.command)
    assert isinstance(after_first.frontier.nodes[0].settlement, SucceededGraphNode)

    with pytest.raises(RuntimeError, match="later:input"):
        await session.next(after_first)
    await session.aclose()
    assert after_first.execution is not None
    fenced = reduce_graph_run(
        after_first,
        FenceGraphExecution(after_first.revision, after_first.execution.token),
    )
    assert isinstance(fenced.frontier.nodes[0].settlement, SucceededGraphNode)
    assert isinstance(fenced.frontier.nodes[1].settlement, PendingGraphNode)


async def test_resource_waiters_preserve_mixed_failure_and_interrupt_outcomes() -> None:
    resource = ResourceId("file")

    async def fail(_value: str) -> NodeFailure:
        return NodeFailure(GraphFailure("a failed"))

    async def interrupt(_value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    class Codec:
        def encode(self, value: str) -> bytes:
            return value.encode()

        def decode(self, payload: bytes) -> str:
            return payload.decode()

    codec = Codec()
    graph: CompiledGraph[str, str] = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resource.mixed-outcomes"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), fail, (resource,)),
                NodeDefinition(GraphNodeId("b"), interrupt, (resource,)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(resource, 0),),
            ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, claimed, session = await prepare_claim(graph, initial)
    awaiting = await drain_session(claimed, session)

    assert isinstance(awaiting.frontier.nodes[0].settlement, FailedGraphNode)
    assert isinstance(awaiting.frontier.nodes[1].settlement, InterruptedGraphNode)
    assert awaiting.execution is awaiting.resources is None


async def test_failure_overrides_survive_resource_admission_per_node() -> None:
    resource = ResourceId("file")
    received: dict[str, list[str]] = {"a": [], "b": []}

    def fail_once(name: str) -> Node[str, str]:
        async def node(value: str):
            received[name].append(value)
            if len(received[name]) == 1:
                return NodeFailure(GraphFailure(f"{name} failed"))
            return NodeSuccess(value)

        return node

    class Codec:
        def encode(self, value: str) -> bytes:
            return value.encode()

        def decode(self, payload: bytes) -> str:
            return payload.decode()

    codec = Codec()
    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.resume"),
            GraphDefinitionVersion(1),
            (
                NodeDefinition(GraphNodeId("a"), fail_once("a"), (resource,)),
                NodeDefinition(GraphNodeId("b"), fail_once("b"), (resource,)),
            ),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
            (ResourceDefinition(resource, 0),),
            ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("run")))
    _executor, claimed, session = await prepare_claim(graph, initial)
    failed = await drain_session(claimed, session)
    resumed = reduce_graph_run(
        failed,
        executor.resume(
            ResumeRequest(
                failed,
                (
                    ResumeFailedNodeRequest(GraphNodeId("a"), OverrideNodeInput("override-a")),
                    ResumeFailedNodeRequest(GraphNodeId("b"), OverrideNodeInput("override-b")),
                ),
            )
        ),
    )
    _executor, retry_claimed, retry_session = await prepare_claim(graph, resumed)
    settled = await drain_session(retry_claimed, retry_session)

    assert received == {"a": ["input", "override-a"], "b": ["input", "override-b"]}
    assert settled.execution is settled.resources is None

import asyncio
from contextvars import ContextVar
from dataclasses import FrozenInstanceError, dataclass, replace
from typing import cast

import pytest

from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.engine.task import GraphTask
from mote_kernel.execution.errors import (
    InvalidRoutingCommandError,
    NodeExecutionContractError,
    ResultCollectionError,
    SnapshotMismatchError,
)
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
    JoinEdge,
    NestedGraphNodeDefinition,
    NodeDefinition,
    NodeFailure,
    NodeInterrupt,
    NodeSuccess,
    ResumeInputBinding,
    compile_graph,
)
from mote_kernel.execution.identity import ExecutionRequestAttemptId
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeNodeRequest,
    ResumeRequest,
    SkipFailedNodeRequest,
    StepRequest,
    UseRequestInput,
)
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.execution.result import (
    AbortedChild,
    AbortedGraph,
    ActiveChild,
    AwaitingResume,
    ChildProjection,
    CompletedChild,
    CompletedGraph,
    ExecutableFrontier,
    MissingChild,
    ReadyToResolve,
    StartMissingChildren,
    TaskFailure,
    TaskResult,
    TaskSuccess,
    WaitForActiveChildren,
    WaitingForChildren,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ContinueGraphRouting,
    FenceGraphExecution,
    GraphAbortReason,
    GraphExecutionAttemptId,
    GraphFailure,
    GraphInterruptId,
    GraphInterruptPayload,
    GraphJoinProgress,
    GraphResumeInputCodecId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphSkipReason,
    GraphStateTransitionError,
    InterruptedGraphNode,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceId,
    ResourceSnapshot,
    child_graph_run_id,
    graph_interrupt_id,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio

REQUEST_ID = ExecutionRequestAttemptId("request")
CHILD_REQUEST_ID = ExecutionRequestAttemptId("child-request")
CHILD_RESOLUTION_REQUEST_ID = ExecutionRequestAttemptId("child-request-2")


async def echo(node_input: str) -> NodeSuccess[str]:
    return NodeSuccess(node_input)


class _Codec:
    def encode(self, value: str) -> bytes:
        return value.encode()

    def decode(self, payload: bytes) -> str:
        return payload.decode()


def graph_with_nodes(
    *nodes: NodeDefinition[str, str] | NestedGraphNodeDefinition[str, str],
    edges: tuple[DirectEdge | ConditionalEdge | JoinEdge, ...] = (),
    entries: tuple[str, ...] = ("a",),
    resources: tuple[ResourceDefinition, ...] = (),
) -> CompiledGraph[str, str]:
    return compile_graph(
        GraphDefinition(
            GraphDefinitionId("test.graph"),
            GraphDefinitionVersion(1),
            nodes,
            edges,
            tuple(GraphNodeId(node_id) for node_id in entries),
            resources,
        )
    )


def started(executor: GraphExecutor[str, str], run_id: str = "run") -> GraphRunState:
    return reduce_graph_run(None, executor.start_command(GraphRunId(run_id)))


async def run_frontier(
    executor: GraphExecutor[str, str],
    graph: CompiledGraph[str, str],
    state: GraphRunState,
    node_input: str,
    projections: tuple[ChildProjection[str], ...] = (),
) -> tuple[GraphRunState, tuple[TaskResult[str], ...]]:
    request = StepRequest(state, node_input, ExecutionRequestAttemptId("request"), projections)
    prepared = await executor.prepare(request)
    assert isinstance(prepared, ExecutableFrontier)
    current = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(current, node_input, ExecutionRequestAttemptId("request"), projections),
    )
    results: list[TaskResult[str]] = []
    try:
        while current.execution is not None:
            result = await session.next(current)
            results.append(result.result)
            current = reduce_graph_run(current, result.command)
    finally:
        await session.aclose()
    return current, tuple(results)


async def run_and_resolve(
    executor: GraphExecutor[str, str],
    graph: CompiledGraph[str, str],
    state: GraphRunState,
    node_input: str,
    projections: tuple[ChildProjection[str], ...] = (),
) -> GraphRunState:
    settled, _results = await run_frontier(executor, graph, state, node_input, projections)
    ready = await executor.prepare(StepRequest(settled, node_input, REQUEST_ID, projections))
    assert isinstance(ready, ReadyToResolve)
    return reduce_graph_run(settled, ready.command)


def nested_graph() -> CompiledGraph[str, str]:
    resource = ResourceId("nested-file")
    child = GraphDefinition(
        GraphDefinitionId("child.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    return graph_with_nodes(
        NodeDefinition(GraphNodeId("ordinary"), echo, (resource,)),
        NestedGraphNodeDefinition(GraphNodeId("nested"), child),
        edges=(DirectEdge(GraphNodeId("nested"), END), DirectEdge(GraphNodeId("ordinary"), END)),
        entries=("nested", "ordinary"),
        resources=(ResourceDefinition(resource, 0),),
    )


async def test_executor_exposes_state_acknowledged_node_stream() -> None:
    calls: list[str] = []

    async def execute(value: str) -> NodeSuccess[str]:
        calls.append(value)
        return NodeSuccess(value.upper())

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), execute),
        NodeDefinition(GraphNodeId("b"), execute),
        edges=(DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
        entries=("a", "b"),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    try:
        first = await session.next(claimed)
        assert isinstance(first.result, TaskSuccess)
        assert first.result.output == "INPUT"
        after_a = reduce_graph_run(claimed, first.command)
        assert after_a.execution is not None
        second = await session.next(after_a)
        after_b = reduce_graph_run(after_a, second.command)
        assert not isinstance(after_b.frontier.nodes[0].settlement, PendingGraphNode)
        assert calls == ["input", "input"]
    finally:
        await session.aclose()


async def test_typed_failure_returns_awaiting_resume_without_retry() -> None:
    calls = 0

    async def fail(value: str) -> NodeFailure:
        nonlocal calls
        calls += 1
        return NodeFailure(GraphFailure(f"failed:{value}"))

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), fail))
    executor = GraphExecutor(graph)
    state, results = await run_frontier(executor, graph, started(executor), "input")
    assert isinstance(results[0], TaskFailure)
    disposition = await executor.prepare(StepRequest(state, "input", REQUEST_ID, ()))
    assert disposition == AwaitingResume((GraphNodeId("a"),), ())
    assert calls == 1


async def test_ordinary_exception_leaves_pending_node_for_exact_fence() -> None:
    async def explode(value: str) -> NodeSuccess[str]:
        raise RuntimeError(value)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), explode))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "boom", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "boom", REQUEST_ID, ()))
    with pytest.raises(RuntimeError, match="boom"):
        await session.next(claimed)
    await session.aclose()
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    settlement = fenced.frontier.nodes[0].settlement
    assert isinstance(settlement, PendingGraphNode)
    assert settlement == PendingGraphNode(settlement.input)


async def test_claim_is_one_shot_and_bound_to_committed_state() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    with pytest.raises(ResultCollectionError, match="committed"):
        await executor.execute(prepared.claim, StepRequest(initial, "input", REQUEST_ID, ()))
    claimed = reduce_graph_run(initial, prepared.claim.command)
    await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    with pytest.raises(ResultCollectionError, match="already"):
        await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))


async def test_concurrent_consumers_of_one_prepared_claim_have_exactly_one_winner() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    request = StepRequest(claimed, "input", REQUEST_ID, ())

    outcomes = await asyncio.gather(
        executor.execute(prepared.claim, request),
        executor.execute(prepared.claim, request),
        return_exceptions=True,
    )
    sessions = tuple(outcome for outcome in outcomes if isinstance(outcome, GraphExecutionSession))
    failures = tuple(outcome for outcome in outcomes if isinstance(outcome, ResultCollectionError))

    assert len(sessions) == len(failures) == 1
    assert "already been consumed" in str(failures[0])
    await sessions[0].aclose()


async def test_missing_and_active_nested_children_block_parent_claim() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)
    active = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (ActiveChild(activation, child),)))
    assert isinstance(active, WaitingForChildren) and isinstance(active.action, WaitForActiveChildren)


async def test_completed_nested_child_is_a_precomputed_completion_on_the_same_path() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)
    child_graph = missing.action.children[0].graph
    completed, _ = await run_frontier(executor, child_graph, child, "child")
    child_resolution = await executor.prepare(StepRequest(completed, "child", ExecutionRequestAttemptId("request"), ()))
    assert isinstance(child_resolution, ReadyToResolve)
    completed = reduce_graph_run(completed, child_resolution.command)
    projection = CompletedChild(activation, completed, "child-output", ContinueGraphRouting())
    prepared = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (projection,)))
    assert isinstance(prepared, ExecutableFrontier)
    resources = prepared.claim.command.resources
    assert resources is not None
    assert tuple(item.node_id for item in resources.acquisitions) == (GraphNodeId("ordinary"),)
    claimed = reduce_graph_run(parent, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, (projection,)))
    try:
        result = await session.next(claimed)
        assert isinstance(result.result, TaskSuccess)
        assert result.result.output == "child-output"
        after = reduce_graph_run(claimed, result.command)
        assert after.resources is not None
        assert after.resources.acquisitions[0].node_id == GraphNodeId("ordinary")
        second = await session.next(after)
        assert second.result.task.node_id == GraphNodeId("ordinary")
        settled = reduce_graph_run(after, second.command)
        assert settled.execution is None
    finally:
        await session.aclose()


async def test_aborted_nested_child_projects_a_typed_failure() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child = reduce_graph_run(None, missing.action.children[0].command)
    aborted = reduce_graph_run(child, AbortGraphRun(child.revision, GraphAbortReason("child aborted")))
    prepared = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (AbortedChild(activation, aborted),)))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(parent, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", REQUEST_ID, (AbortedChild(activation, aborted),)),
    )
    try:
        result = await session.next(claimed)
        assert isinstance(result.result, TaskFailure)
        assert result.result.failure == GraphFailure("child aborted")
    finally:
        await session.aclose()


async def test_nested_projection_requires_terminal_child_state() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    active = reduce_graph_run(None, missing.action.children[0].command)
    with pytest.raises(ResultCollectionError, match="completed child"):
        await executor.prepare(
            StepRequest(
                parent, "input", REQUEST_ID, (CompletedChild(activation, active, "forged", ContinueGraphRouting()),)
            )
        )


async def test_concurrent_runs_share_executor_without_cross_run_state() -> None:
    barrier = asyncio.Barrier(2)

    async def execute(value: str) -> NodeSuccess[str]:
        await barrier.wait()
        return NodeSuccess(value)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), execute))
    executor = GraphExecutor(graph)
    first, second = started(executor, "first"), started(executor, "second")
    first_p, second_p = await asyncio.gather(
        executor.prepare(StepRequest(first, "first", REQUEST_ID, ())),
        executor.prepare(StepRequest(second, "second", REQUEST_ID, ())),
    )
    assert isinstance(first_p, ExecutableFrontier) and isinstance(second_p, ExecutableFrontier)
    first_claimed = reduce_graph_run(first, first_p.claim.command)
    second_claimed = reduce_graph_run(second, second_p.claim.command)
    first_session, second_session = await asyncio.gather(
        executor.execute(first_p.claim, StepRequest(first_claimed, "first", REQUEST_ID, ())),
        executor.execute(second_p.claim, StepRequest(second_claimed, "second", REQUEST_ID, ())),
    )
    try:
        one, two = await asyncio.gather(first_session.next(first_claimed), second_session.next(second_claimed))
        assert isinstance(one.result, TaskSuccess)
        assert isinstance(two.result, TaskSuccess)
        assert one.result.output == "first"
        assert two.result.output == "second"
        assert one.result.task.task_id != two.result.task.task_id
    finally:
        await asyncio.gather(first_session.aclose(), second_session.aclose())


async def test_context_and_input_identity_are_isolated_per_task() -> None:
    trace = ContextVar("trace", default="missing")

    async def read(value: str) -> NodeSuccess[str]:
        return NodeSuccess(f"{trace.get()}:{value}")

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), read),
        NodeDefinition(GraphNodeId("b"), read),
        entries=("a", "b"),
    )
    token = trace.set("caller")
    try:
        executor = GraphExecutor(graph)
        state = started(executor)
        prepared = await executor.prepare(StepRequest(state, "input", REQUEST_ID, ()))
        assert isinstance(prepared, ExecutableFrontier)
        claimed = reduce_graph_run(state, prepared.claim.command)
        session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
        try:
            first = await session.next(claimed)
            assert isinstance(first.result, TaskSuccess)
            assert first.result.output == "caller:input"
        finally:
            await session.aclose()
        assert trace.get() == "caller"
    finally:
        trace.reset(token)


async def test_node_contract_error_is_not_forged_into_settlement() -> None:
    async def invalid(value: str):
        return cast(object, value)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), invalid))  # type: ignore[arg-type]
    executor = GraphExecutor(graph)
    state = started(executor)
    prepared = await executor.prepare(StepRequest(state, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    try:
        with pytest.raises(NodeExecutionContractError):
            await session.next(claimed)
    finally:
        await session.aclose()


async def test_prepare_reports_terminal_and_settled_dispositions_without_claiming() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    try:
        result = await session.next(claimed)
        settled = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    ready = await executor.prepare(StepRequest(settled, "input", REQUEST_ID, ()))
    assert isinstance(ready, ReadyToResolve)
    completed = reduce_graph_run(settled, ready.command)
    assert completed.status is GraphRunStatus.COMPLETED
    terminal = await executor.prepare(StepRequest(completed, "input", REQUEST_ID, ()))
    assert isinstance(terminal, CompletedGraph)

    aborted = reduce_graph_run(initial, AbortGraphRun(initial.revision, GraphAbortReason("operator")))
    aborted_disposition = await executor.prepare(StepRequest(aborted, "input", REQUEST_ID, ()))
    assert isinstance(aborted_disposition, AbortedGraph)


async def test_prepare_rejects_reentry_into_an_active_execution() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    with pytest.raises(ResultCollectionError, match="original execution session"):
        await executor.prepare(StepRequest(claimed, "input", REQUEST_ID, ()))


async def test_resume_projection_validates_each_action_variant_and_lifecycle() -> None:
    async def fail(value: str) -> NodeFailure:
        return NodeFailure(GraphFailure("failed"))

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), fail))
    executor = GraphExecutor(graph)
    initial = started(executor)
    state, _results = await run_frontier(executor, graph, initial, "input")
    with pytest.raises(SnapshotMismatchError, match="non-empty"):
        executor.resume(ResumeRequest(state, ()))
    with pytest.raises(SnapshotMismatchError, match="unsupported action"):
        executor.resume(ResumeRequest(state, (cast(ResumeNodeRequest[str], object()),)))
    with pytest.raises(SnapshotMismatchError, match="distinct"):
        executor.resume(
            ResumeRequest(
                state,
                (
                    ResumeFailedNodeRequest(GraphNodeId("a"), OverrideNodeInput("one")),
                    ResumeFailedNodeRequest(GraphNodeId("a"), OverrideNodeInput("two")),
                ),
            )
        )
    with pytest.raises(SnapshotMismatchError, match="unknown frontier"):
        executor.resume(
            ResumeRequest(state, (ResumeFailedNodeRequest(GraphNodeId("missing"), OverrideNodeInput("retry")),))
        )


async def test_executor_rejects_graph_ownership_and_parent_shape_mismatches() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    foreign = replace(initial, definition_id=GraphDefinitionId("foreign.graph"))
    with pytest.raises(SnapshotMismatchError, match="owned"):
        await executor.prepare(StepRequest(foreign, "input", REQUEST_ID, ()))

    root_with_parent = replace(
        initial,
        parent=ParentGraphActivation(GraphRunId("parent"), 0, GraphNodeId("nested")),
    )
    with pytest.raises(SnapshotMismatchError, match="root graph"):
        await executor.prepare(StepRequest(root_with_parent, "input", REQUEST_ID, ()))

    nested_executor = GraphExecutor(nested_graph())
    nested_root = started(nested_executor)
    child_without_parent = replace(nested_root, definition_id=GraphDefinitionId("child.graph"))
    with pytest.raises(SnapshotMismatchError, match="nested graph"):
        await nested_executor.prepare(StepRequest(child_without_parent, "input", REQUEST_ID, ()))

    shared_child = GraphDefinition(
        GraphDefinitionId("shared.child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (),
        (GraphNodeId("child"),),
    )
    shared_parent = GraphDefinition(
        GraphDefinitionId("shared.parent"),
        GraphDefinitionVersion(1),
        (
            NestedGraphNodeDefinition(GraphNodeId("first"), shared_child),
            NestedGraphNodeDefinition(GraphNodeId("second"), shared_child),
        ),
        (),
        (GraphNodeId("first"), GraphNodeId("second")),
    )
    GraphExecutor(compile_graph(shared_parent))


async def test_resume_rejects_non_failed_and_invalid_input_variants() -> None:
    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), node))
    executor = GraphExecutor(graph)
    initial = started(executor)
    with pytest.raises(SnapshotMismatchError, match="failure resume"):
        executor.resume(ResumeRequest(initial, (ResumeFailedNodeRequest(GraphNodeId("a"), OverrideNodeInput("x")),)))
    with pytest.raises(SnapshotMismatchError, match="interrupt resume"):
        executor.resume(
            ResumeRequest(
                initial,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        GraphInterruptId("interrupt"),
                        OverrideNodeInput("x"),
                    ),
                ),
            )
        )
    with pytest.raises(SnapshotMismatchError, match="skip"):
        executor.resume(
            ResumeRequest(
                initial,
                (
                    SkipFailedNodeRequest(
                        GraphNodeId("a"),
                        GraphSkipReason("skip"),
                        ContinueGraphRouting(),
                    ),
                ),
            )
        )


async def test_resume_projection_covers_override_default_skip_and_interrupt_input_guards() -> None:
    async def fail(value: str) -> NodeFailure:
        return NodeFailure(GraphFailure("failed"))

    codec = _Codec()
    resumable = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resumable.graph"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), fail),),
            (),
            (GraphNodeId("a"),),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    executor = GraphExecutor(resumable)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    with pytest.raises(SnapshotMismatchError, match="quiescent"):
        executor.resume(ResumeRequest(claimed, ()))
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    try:
        result = await session.next(claimed)
        failed = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()

    override = executor.resume(
        ResumeRequest(failed, (ResumeFailedNodeRequest(GraphNodeId("a"), OverrideNodeInput("retry")),))
    )
    assert override.actions[0].node_id == GraphNodeId("a")
    default = executor.resume(ResumeRequest(failed, (ResumeFailedNodeRequest(GraphNodeId("a"), UseRequestInput()),)))
    assert default.actions[0].node_id == GraphNodeId("a")
    with pytest.raises(SnapshotMismatchError, match="unsupported variant"):
        executor.resume(
            ResumeRequest(
                failed,
                (
                    ResumeFailedNodeRequest(
                        GraphNodeId("a"),
                        cast(UseRequestInput | OverrideNodeInput[str], object()),
                    ),
                ),
            )
        )
    skip = executor.resume(
        ResumeRequest(
            failed,
            (SkipFailedNodeRequest(GraphNodeId("a"), GraphSkipReason("operator"), ContinueGraphRouting()),),
        )
    )
    assert skip.actions[0].node_id == GraphNodeId("a")

    async def interrupt(value: str) -> NodeInterrupt:
        return NodeInterrupt(GraphInterruptPayload(b"question"))

    interrupted_graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("resumable.interrupt"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), interrupt),),
            (DirectEdge(GraphNodeId("a"), END),),
            (GraphNodeId("a"),),
            resume_input=ResumeInputBinding(GraphResumeInputCodecId("input.v1"), 1, codec, codec),
        )
    )
    interrupt_executor = GraphExecutor(interrupted_graph)
    interrupt_initial = started(interrupt_executor)
    interrupt_prepared = await interrupt_executor.prepare(StepRequest(interrupt_initial, "input", REQUEST_ID, ()))
    assert isinstance(interrupt_prepared, ExecutableFrontier)
    interrupt_claimed = reduce_graph_run(interrupt_initial, interrupt_prepared.claim.command)
    interrupt_session = await interrupt_executor.execute(
        interrupt_prepared.claim,
        StepRequest(interrupt_claimed, "input", REQUEST_ID, ()),
    )
    try:
        interrupt_result = await interrupt_session.next(interrupt_claimed)
        interrupted = reduce_graph_run(interrupt_claimed, interrupt_result.command)
    finally:
        await interrupt_session.aclose()
    interrupt_settlement = interrupted.frontier.nodes[0].settlement
    assert isinstance(interrupt_settlement, InterruptedGraphNode)
    identity = interrupt_settlement.interrupt.identity
    interrupt_id = graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )
    with pytest.raises(SnapshotMismatchError, match="unsupported variant"):
        interrupt_executor.resume(
            ResumeRequest(
                interrupted,
                (
                    ResumeInterruptedNodeRequest(
                        GraphNodeId("a"),
                        interrupt_id,
                        cast(OverrideNodeInput[str], object()),
                    ),
                ),
            )
        )


async def test_prepare_rejects_an_empty_resource_admission_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    resource = ResourceId("file")

    async def node(value: str) -> NodeSuccess[str]:
        return NodeSuccess(value)

    graph = compile_graph(
        GraphDefinition(
            GraphDefinitionId("resource.empty-admission"),
            GraphDefinitionVersion(1),
            (NodeDefinition(GraphNodeId("a"), node, (resource,)),),
            (),
            (GraphNodeId("a"),),
            (ResourceDefinition(resource, 0),),
        )
    )
    import mote_kernel.execution.engine.superstep as superstep_module
    from mote_kernel.execution.engine.admission import TaskAdmission

    def empty_admission(
        _graph: CompiledGraph[str, str],
        _tasks: tuple[GraphTask, ...],
        snapshot: ResourceSnapshot,
    ) -> TaskAdmission:
        return TaskAdmission(snapshot, (), ())

    monkeypatch.setattr(
        superstep_module,
        "admit_tasks",
        empty_admission,
    )
    executor = GraphExecutor(graph)
    state = started(executor)
    with pytest.raises(ResultCollectionError, match="did not create acquisition"):
        await executor.prepare(StepRequest(state, "input", REQUEST_ID, ()))


async def test_nested_invalid_completion_enters_error_draining() -> None:
    child = GraphDefinition(
        GraphDefinitionId("nested.error.child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    parent_graph = graph_with_nodes(
        NestedGraphNodeDefinition(GraphNodeId("nested"), child),
        edges=(ConditionalEdge(GraphNodeId("nested"), GraphRouteId("done"), END),),
        entries=("nested",),
    )
    executor = GraphExecutor(parent_graph)
    parent = started(executor)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child_state = reduce_graph_run(None, missing.action.children[0].command)
    child_prepared = await executor.prepare(StepRequest(child_state, "child", CHILD_REQUEST_ID, ()))
    assert isinstance(child_prepared, ExecutableFrontier)
    claimed_child = reduce_graph_run(child_state, child_prepared.claim.command)
    child_session = await executor.execute(
        child_prepared.claim,
        StepRequest(claimed_child, "child", CHILD_REQUEST_ID, ()),
    )
    try:
        child_result = await child_session.next(claimed_child)
        completed_child = reduce_graph_run(claimed_child, child_result.command)
    finally:
        await child_session.aclose()
    child_ready = await executor.prepare(StepRequest(completed_child, "child", CHILD_RESOLUTION_REQUEST_ID, ()))
    assert isinstance(child_ready, ReadyToResolve)
    completed_child = reduce_graph_run(completed_child, child_ready.command)
    projection = CompletedChild(activation, completed_child, "output", ContinueGraphRouting())
    prepared = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (projection,)))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(parent, prepared.claim.command)
    session = await executor.execute(
        prepared.claim,
        StepRequest(claimed, "input", REQUEST_ID, (projection,)),
    )
    try:
        with pytest.raises(InvalidRoutingCommandError):
            await session.next(claimed)
    finally:
        await session.aclose()


async def test_prepared_claim_remains_bound_to_executor_and_request_identity() -> None:
    calls = 0

    async def node(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), node))
    owner = GraphExecutor(graph)
    other = GraphExecutor(graph)
    initial = started(owner)
    prepared = await owner.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await other.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await owner.execute(
            prepared.claim,
            StepRequest(claimed, "input", ExecutionRequestAttemptId("other-request"), ()),
        )
    assert not prepared.claim.consumed
    assert calls == 0

    session = await owner.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    try:
        completed = await session.next(claimed)
        assert isinstance(completed.result, TaskSuccess)
        assert completed.result.output == "input"
    finally:
        await session.aclose()
    assert prepared.claim.consumed
    assert calls == 1


async def test_fenced_unstarted_claim_cannot_start_or_be_consumed() -> None:
    calls = 0

    async def node(node_input: str) -> NodeSuccess[str]:
        nonlocal calls
        calls += 1
        return NodeSuccess(node_input)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), node))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(prepared.claim, StepRequest(fenced, "input", REQUEST_ID, ()))
    assert not prepared.claim.consumed
    assert calls == 0


async def test_parallel_context_mutations_are_isolated_and_request_input_is_frozen() -> None:
    @dataclass(frozen=True, slots=True)
    class InputSnapshot:
        value: str

    trace = ContextVar("parallel-trace", default="missing")
    barrier = asyncio.Barrier(2)
    observed: list[InputSnapshot] = []

    def definition(name: str) -> NodeDefinition[InputSnapshot, str]:
        async def node(node_input: InputSnapshot) -> NodeSuccess[str]:
            assert trace.get() == "caller"
            observed.append(node_input)
            trace.set(name)
            await asyncio.wait_for(barrier.wait(), timeout=1)
            return NodeSuccess(f"{trace.get()}:{node_input.value}")

        return NodeDefinition(GraphNodeId(name), node)

    graph = compile_graph(
        GraphDefinition[InputSnapshot, str](
            GraphDefinitionId("context.graph"),
            GraphDefinitionVersion(1),
            (definition("a"), definition("b")),
            (DirectEdge(GraphNodeId("a"), END), DirectEdge(GraphNodeId("b"), END)),
            (GraphNodeId("a"), GraphNodeId("b")),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, executor.start_command(GraphRunId("context-run")))
    node_input = InputSnapshot("input")
    token = trace.set("caller")
    try:
        prepared = await executor.prepare(StepRequest(initial, node_input, REQUEST_ID, ()))
        assert isinstance(prepared, ExecutableFrontier)
        current = reduce_graph_run(initial, prepared.claim.command)
        session = await executor.execute(prepared.claim, StepRequest(current, node_input, REQUEST_ID, ()))
        outputs: list[str] = []
        try:
            for _ in range(2):
                completed = await session.next(current)
                assert isinstance(completed.result, TaskSuccess)
                outputs.append(completed.result.output)
                current = reduce_graph_run(current, completed.command)
        finally:
            await session.aclose()
        assert trace.get() == "caller"
    finally:
        trace.reset(token)

    assert sorted(outputs) == ["a:input", "b:input"]
    assert len(observed) == 2 and all(item is node_input for item in observed)
    field = "value"
    with pytest.raises(FrozenInstanceError):
        setattr(observed[0], field, "changed")


async def test_node_success_subclass_uses_the_normal_completion_path() -> None:
    class SpecializedSuccess(NodeSuccess[str]):
        pass

    async def node(node_input: str) -> NodeSuccess[str]:
        return SpecializedSuccess(node_input)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), node))
    executor = GraphExecutor(graph)
    current = started(executor)
    settled, results = await run_frontier(executor, graph, current, "input")
    assert len(results) == 1 and isinstance(results[0], TaskSuccess)
    assert results[0].output == "input"
    assert settled.execution is None


async def test_nested_graph_can_prepare_a_grandchild_with_exact_parent_coordinates() -> None:
    leaf = GraphDefinition[str, str](
        GraphDefinitionId("leaf.graph"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("leaf"), echo),),
        (DirectEdge(GraphNodeId("leaf"), END),),
        (GraphNodeId("leaf"),),
    )
    child = GraphDefinition[str, str](
        GraphDefinitionId("grandchild.parent"),
        GraphDefinitionVersion(1),
        (NestedGraphNodeDefinition(GraphNodeId("child"), leaf),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    root = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("grandchild.root"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(GraphNodeId("root"), child),),
            (DirectEdge(GraphNodeId("root"), END),),
            (GraphNodeId("root"),),
        )
    )
    executor = GraphExecutor(root)
    root_state = reduce_graph_run(None, executor.start_command(GraphRunId("nested-run")))
    child_activation = ParentGraphActivation(root_state.run_id, 0, GraphNodeId("root"))
    child_wait = await executor.prepare(StepRequest(root_state, "input", REQUEST_ID, (MissingChild(child_activation),)))
    assert isinstance(child_wait, WaitingForChildren) and isinstance(child_wait.action, StartMissingChildren)
    child_state = reduce_graph_run(None, child_wait.action.children[0].command)
    grandchild_activation = ParentGraphActivation(child_state.run_id, 0, GraphNodeId("child"))

    grandchild_wait = await executor.prepare(
        StepRequest(child_state, "input", REQUEST_ID, (MissingChild(grandchild_activation),))
    )
    assert isinstance(grandchild_wait, WaitingForChildren)
    assert isinstance(grandchild_wait.action, StartMissingChildren)
    grandchild = grandchild_wait.action.children[0]
    assert grandchild.command.parent == grandchild_activation
    assert grandchild.command.run_id == child_graph_run_id(
        child_state.run_id,
        child_state.superstep,
        GraphNodeId("child"),
    )


async def test_nested_child_start_preserves_all_canonical_entry_nodes() -> None:
    child = GraphDefinition[str, str](
        GraphDefinitionId("entries.child"),
        GraphDefinitionVersion(1),
        tuple(NodeDefinition(GraphNodeId(node_id), echo) for node_id in ("c", "a", "b")),
        tuple(DirectEdge(GraphNodeId(node_id), END) for node_id in ("a", "b", "c")),
        tuple(GraphNodeId(node_id) for node_id in ("c", "a", "b")),
    )
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("entries.parent"),
            GraphDefinitionVersion(1),
            (NestedGraphNodeDefinition(GraphNodeId("nested"), child),),
            (DirectEdge(GraphNodeId("nested"), END),),
            (GraphNodeId("nested"),),
        )
    )
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("entry-run")))
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child_state = reduce_graph_run(None, missing.action.children[0].command)
    assert tuple(node.node_id for node in child_state.frontier.nodes) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
        GraphNodeId("c"),
    )


async def test_nested_completion_contributes_to_a_cross_superstep_join() -> None:
    child = GraphDefinition[str, str](
        GraphDefinitionId("join.child"),
        GraphDefinitionVersion(1),
        (NodeDefinition(GraphNodeId("child"), echo),),
        (DirectEdge(GraphNodeId("child"), END),),
        (GraphNodeId("child"),),
    )
    graph = compile_graph(
        GraphDefinition[str, str](
            GraphDefinitionId("join.parent"),
            GraphDefinitionVersion(1),
            (
                NestedGraphNodeDefinition(GraphNodeId("a"), child),
                NodeDefinition(GraphNodeId("b"), echo),
                NodeDefinition(GraphNodeId("joined"), echo),
            ),
            (
                DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
                JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("joined")),
                DirectEdge(GraphNodeId("joined"), END),
            ),
            (GraphNodeId("a"),),
        )
    )
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, executor.start_command(GraphRunId("join-run")))
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("a"))
    missing = await executor.prepare(StepRequest(parent, "input", REQUEST_ID, (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren) and isinstance(missing.action, StartMissingChildren)
    child_state = reduce_graph_run(None, missing.action.children[0].command)
    completed_child = await run_and_resolve(executor, missing.action.children[0].graph, child_state, "child")

    after_child = await run_and_resolve(
        executor,
        graph,
        parent,
        "input",
        (CompletedChild(activation, completed_child, "child-output", ContinueGraphRouting()),),
    )
    assert tuple(node.node_id for node in after_child.frontier.nodes) == (GraphNodeId("b"),)
    assert after_child.join_progress == (
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("joined"),
            frozenset({GraphNodeId("a")}),
        ),
    )

    after_b = await run_and_resolve(executor, graph, after_child, "input")
    assert tuple(node.node_id for node in after_b.frontier.nodes) == (GraphNodeId("joined"),)
    assert after_b.join_progress == ()


async def test_claim_scope_uses_canonical_node_order_for_different_lengths() -> None:
    barrier = asyncio.Barrier(2)

    async def node(value: str) -> NodeSuccess[str]:
        await asyncio.wait_for(barrier.wait(), timeout=1)
        return NodeSuccess(value)

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("aa"), node),
        NodeDefinition(GraphNodeId("z"), node),
        edges=(DirectEdge(GraphNodeId("aa"), END), DirectEdge(GraphNodeId("z"), END)),
        entries=("aa", "z"),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    assert prepared.claim.snapshot.node_ids == (GraphNodeId("aa"), GraphNodeId("z"))
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    try:
        first = await session.next(claimed)
        after_first = reduce_graph_run(claimed, first.command)
        second = await session.next(after_first)
    finally:
        await session.aclose()

    assert (first.result.task.node_id, second.result.task.node_id) == (GraphNodeId("aa"), GraphNodeId("z"))


async def test_late_settlement_cannot_overwrite_a_reclaimed_generation() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    first = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(first, ExecutableFrontier)
    first_state = reduce_graph_run(initial, first.claim.command)
    first_session = await executor.execute(first.claim, StepRequest(first_state, "input", REQUEST_ID, ()))
    late = await first_session.next(first_state)
    await first_session.aclose()
    assert first_state.execution is not None
    fenced = reduce_graph_run(
        first_state,
        FenceGraphExecution(first_state.revision, first_state.execution.token),
    )
    second = await executor.prepare(StepRequest(fenced, "input", REQUEST_ID, ()))
    assert isinstance(second, ExecutableFrontier)
    second_state = reduce_graph_run(fenced, second.claim.command)

    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(second_state, late.command)
    assert second_state.execution is not None
    assert second_state.execution.token.generation == 2


async def test_cancelled_session_retains_exact_lease_for_fence_and_reclaim() -> None:
    entered = asyncio.Event()

    async def wait(value: str) -> NodeSuccess[str]:
        entered.set()
        await asyncio.sleep(10)
        return NodeSuccess(value)

    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), wait))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))
    running = asyncio.create_task(session.next(claimed))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert session.quiescent
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    retry = await executor.prepare(StepRequest(fenced, "retry", REQUEST_ID, ()))
    assert isinstance(retry, ExecutableFrontier)
    retried = reduce_graph_run(fenced, retry.claim.command)
    assert retried.execution is not None
    assert retried.execution.token.generation == 2


async def test_node_initiated_cancellation_waits_for_sibling_cleanup() -> None:
    sibling_started = asyncio.Event()
    sibling_cleaned = asyncio.Event()

    async def sibling(value: str) -> NodeSuccess[str]:
        sibling_started.set()
        try:
            await asyncio.sleep(10)
        finally:
            sibling_cleaned.set()
        return NodeSuccess(value)

    async def cancel(value: str) -> NodeSuccess[str]:
        del value
        await sibling_started.wait()
        raise asyncio.CancelledError

    graph = graph_with_nodes(
        NodeDefinition(GraphNodeId("a"), sibling),
        NodeDefinition(GraphNodeId("b"), cancel),
        entries=("a", "b"),
    )
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, StepRequest(claimed, "input", REQUEST_ID, ()))

    with pytest.raises(asyncio.CancelledError):
        await session.next(claimed)

    assert sibling_cleaned.is_set()
    assert session.quiescent
    assert claimed.execution is not None


async def test_claim_guard_rejects_a_forged_committed_attempt_token() -> None:
    graph = graph_with_nodes(NodeDefinition(GraphNodeId("a"), echo))
    executor = GraphExecutor(graph)
    initial = started(executor)
    prepared = await executor.prepare(StepRequest(initial, "input", REQUEST_ID, ()))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    assert claimed.execution is not None
    forged = replace(
        claimed,
        execution=replace(
            claimed.execution,
            token=replace(
                claimed.execution.token,
                attempt_id=GraphExecutionAttemptId("forged"),
            ),
        ),
    )

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(prepared.claim, StepRequest(forged, "input", REQUEST_ID, ()))
    assert not prepared.claim.consumed

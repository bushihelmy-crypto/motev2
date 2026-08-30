import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TypeAlias, TypeVar, cast

import pytest

import mote_kernel.execution.engine.admission as admission_module
import mote_kernel.execution.engine.frontier as frontier_module
import mote_kernel.execution.engine.superstep as superstep_module
from mote_kernel.execution import Graph
from mote_kernel.execution.engine.admission import TaskAdmission, admit_graph_input
from mote_kernel.execution.engine.frontier import FrontierPreparation
from mote_kernel.execution.engine.resume_admission import prepare_resume
from mote_kernel.execution.engine.session import GraphExecutionSession
from mote_kernel.execution.engine.superstep import ExecutableFrontier
from mote_kernel.execution.engine.task import GraphTask, TaskId
from mote_kernel.execution.errors import (
    GraphValidationError,
    GraphValueAdmissionError,
    NodeExecutionContractError,
    ResultCollectionError,
    SnapshotMismatchError,
)
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.family_driver import fresh_root
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    NodeOutputRef,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph.values import (
    GraphOutputView,
    NamedValue,
    NodeInputFrame,
    _frame_value,
    _make_graph_output_view,
)
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.identity import (
    ScopeRunCoordinate,
    child_scope_run_for_activation,
    root_scope_run,
)
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.request import (
    OverrideNodeInput,
    ResumeFailedNodeRequest,
    ResumeInterruptedNodeRequest,
    ResumeRequest,
    SkipFailedNodeRequest,
    StepRequest,
    UseMaterializedInput,
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
    MissingChild,
    ReadyToResolve,
    TaskFailure,
    TaskResult,
    TaskSuccess,
    WaitingForChildren,
)
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    GraphInputAvailabilityCoordinate,
    ScopedFrameIndex,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    CompleteGraphFrontier,
    ContinueGraphRouting,
    FenceGraphExecution,
    GraphAbortReason,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphInterruptId,
    GraphJoinProgress,
    GraphNodeId,
    GraphResumeInputCodecId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    InterruptedGraphNode,
    ParentGraphActivation,
    PendingGraphNode,
    ResourceId,
    ResourceSnapshot,
    SettleGraphNode,
    SucceededGraphNodeOutcome,
    child_graph_run_id,
    graph_interrupt_id,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio

GraphValueT = TypeVar("GraphValueT")
DEFAULT_LIMITS = ExecutionLimits()


async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


class _Codec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


class _TrackingCodec:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.tamper: str | None = None

    def encode(self, value: Graph.Values[str]) -> bytes:
        self.events.append("encode")
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        self.events.append("decode")
        if self.tamper == "name":
            return Graph.values(other="input")
        if self.tamper == "type":
            return cast(Graph.Values[str], Graph.values(value=True))
        return Graph.values(value=payload.decode())


def node(
    node_id: str,
    operation: NodeCallable[str] = echo,
    *,
    inputs: dict[str, GraphInputRef[str] | NodeOutputRef] | None = None,
    resources: tuple[ResourceId, ...] = (),
) -> CallableNodeDefinition[str]:
    bindings = {"value": Graph.graph_input("value", str)} if inputs is None else inputs
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        operation,
        normalize_input_bindings(bindings),
        normalize_output_declarations({"value": str}),
        resources,
    )


def graph_with_nodes(
    *nodes: CallableNodeDefinition[str] | NestedGraphNodeDefinition[str],
    edges: tuple[ConditionalEdge | DirectEdge | JoinEdge, ...] = (),
    entries: tuple[str, ...] = (),
    resources: tuple[ResourceDefinition, ...] = (),
    definition_id: str = "test.graph",
    resume_input: ResumeInputBinding[str] | None = None,
) -> CompiledGraph[str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId(definition_id),
            version=GraphDefinitionVersion(1),
            nodes=nodes,
            edges=edges,
            entries=tuple(GraphNodeId(node_id) for node_id in entries),
            outputs=normalize_graph_output_declarations({}),
            resources=resources,
            resume_input=resume_input,
        )
    )


def request_with_values(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    values: Graph.Values[GraphValueT],
    projections: tuple[ChildProjection[GraphValueT], ...] = (),
    *,
    limits: ExecutionLimits = DEFAULT_LIMITS,
) -> StepRequest[GraphValueT]:
    frame = admit_graph_input(graph, values)
    frames: ScopedFrameIndex[GraphValueT] = ScopedFrameIndex()
    frames = frames.add_graph_input(
        AdmittedGraphInput(
            GraphInputAvailabilityCoordinate(scope_run, graph.graph_input_descriptor.identity),
            frame,
        )
    )
    return StepRequest(state, scope_run, frames, projections, limits)


def string_request(
    graph: CompiledGraph[str],
    state: GraphRunState,
    value: str,
    projections: tuple[ChildProjection[str], ...] = (),
    *,
    scope_run: ScopeRunCoordinate | None = None,
    limits: ExecutionLimits = DEFAULT_LIMITS,
) -> StepRequest[str]:
    coordinate = root_scope_run(state.run_id) if scope_run is None else scope_run
    return request_with_values(
        graph,
        state,
        coordinate,
        Graph.values(value=value),
        projections,
        limits=limits,
    )


def started(graph: CompiledGraph[str], run_id: str = "run") -> GraphRunState:
    return reduce_graph_run(None, project_start_graph_command(graph, GraphRunId(run_id)))


def output_value(result: TaskSuccess[str]) -> str:
    return _frame_value(result.output, "value")


def child_output(graph: CompiledGraph[str], value: str) -> GraphOutputView[str]:
    declarations = tuple((item.name, item.descriptor) for item in graph.graph_output_descriptor.declarations.entries)
    return _make_graph_output_view((NamedValue("value", value),), declarations)


async def run_frontier(
    executor: GraphExecutor[str],
    graph: CompiledGraph[str],
    state: GraphRunState,
    node_input: str,
    projections: tuple[ChildProjection[str], ...] = (),
    *,
    scope_run: ScopeRunCoordinate | None = None,
) -> tuple[GraphRunState, tuple[TaskResult[str], ...]]:
    execution_request = string_request(
        graph,
        state,
        node_input,
        projections,
        scope_run=scope_run,
    )
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    current = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(prepared.claim, current)
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
    executor: GraphExecutor[str],
    graph: CompiledGraph[str],
    state: GraphRunState,
    node_input: str,
    projections: tuple[ChildProjection[str], ...] = (),
    *,
    scope_run: ScopeRunCoordinate | None = None,
) -> GraphRunState:
    settled, _results = await run_frontier(
        executor,
        graph,
        state,
        node_input,
        projections,
        scope_run=scope_run,
    )
    ready = await executor.prepare(
        string_request(
            graph,
            settled,
            node_input,
            projections,
            scope_run=scope_run,
        )
    )
    assert isinstance(ready, ReadyToResolve)
    return reduce_graph_run(settled, ready.command)


def child_definition(definition_id: str = "child.graph") -> GraphDefinition[str]:
    return GraphDefinition(
        definition_id=GraphDefinitionId(definition_id),
        version=GraphDefinitionVersion(1),
        nodes=(node("child"),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({"value": Graph.node_output("child", "value")}),
    )


def nested_node(node_id: str, child: GraphDefinition[str]) -> NestedGraphNodeDefinition[str]:
    return NestedGraphNodeDefinition(
        GraphNodeId(node_id),
        child,
        normalize_input_bindings({"value": Graph.graph_input("value", str)}),
    )


def nested_graph() -> CompiledGraph[str]:
    resource = ResourceId("nested-file")
    return graph_with_nodes(
        node("ordinary", resources=(resource,)),
        nested_node("nested", child_definition()),
        resources=(ResourceDefinition(resource, 0),),
    )


def started_nested_child(
    parent_graph: CompiledGraph[str],
    parent_state: GraphRunState,
    parent_scope: ScopeRunCoordinate,
    node_id: GraphNodeId,
) -> tuple[ParentGraphActivation, CompiledGraph[str], ScopeRunCoordinate, GraphRunState]:
    activation = ParentGraphActivation(parent_state.run_id, parent_state.superstep, node_id)
    child_graph = parent_graph.nested_graphs[node_id]
    coordinate = child_scope_run_for_activation(parent_scope, activation)
    command = project_start_graph_command(child_graph, coordinate.graph_run_id, activation)
    return activation, child_graph, coordinate, reduce_graph_run(None, command)


def completed_child(state: GraphRunState) -> GraphRunState:
    claimed = reduce_graph_run(
        state,
        ClaimGraphExecution(state.revision, GraphExecutionAttemptId("child-attempt"), None),
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


async def test_executor_exposes_state_acknowledged_node_stream() -> None:
    calls: list[str] = []

    async def execute(values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append(values["value"])
        return Graph.values(value=values["value"].upper())

    graph = graph_with_nodes(node("a", execute), node("b", execute))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        first = await session.next(claimed)
        assert isinstance(first.result, TaskSuccess)
        assert output_value(first.result) == "INPUT"
        after_a = reduce_graph_run(claimed, first.command)
        assert after_a.execution is not None
        second = await session.next(after_a)
        after_b = reduce_graph_run(after_a, second.command)
        assert not isinstance(after_b.frontier.nodes[0].settlement, PendingGraphNode)
        assert calls == ["input", "input"]
    finally:
        await session.aclose()


async def test_frontier_preparation_and_inputs_are_reused_by_session(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_calls = 0
    materialize_calls = 0
    original_prepare = superstep_module.prepare_frontier
    original_materialize = frontier_module.materialize_node_input

    def track_prepare(graph: CompiledGraph[str], request: StepRequest[str]) -> FrontierPreparation[str]:
        nonlocal prepare_calls
        prepare_calls += 1
        return original_prepare(graph, request)

    def track_materialize(
        graph: CompiledGraph[str],
        state: GraphRunState,
        scope_run: ScopeRunCoordinate,
        frames: ScopedFrameIndex[str],
        node_id: GraphNodeId,
    ) -> NodeInputFrame[str]:
        nonlocal materialize_calls
        materialize_calls += 1
        return original_materialize(graph, state, scope_run, frames, node_id)

    monkeypatch.setattr(superstep_module, "prepare_frontier", track_prepare)
    monkeypatch.setattr(frontier_module, "materialize_node_input", track_materialize)
    graph = graph_with_nodes(node("a"))
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    executor = GraphExecutor(graph)

    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        completed = await session.next(claimed)
        assert isinstance(completed.result, TaskSuccess)
    finally:
        await session.aclose()

    assert prepare_calls == 1
    assert materialize_calls == 1


async def test_prepare_rejects_wrong_scope_or_graph_run_identity() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    state = started(graph)
    wrong_scope = ScopeRunCoordinate((GraphNodeId("nested"),), state.run_id)
    wrong_run = root_scope_run(GraphRunId("other-run"))

    with pytest.raises(SnapshotMismatchError, match="scope-run coordinate"):
        await executor.prepare(string_request(graph, state, "input", scope_run=wrong_scope))
    with pytest.raises(SnapshotMismatchError, match="scope-run coordinate"):
        await executor.prepare(string_request(graph, state, "input", scope_run=wrong_run))


async def test_execute_scope_rejection_does_not_consume_prepared_claim() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    request = string_request(graph, initial, "input")
    prepared = await executor.prepare(request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    wrong_run = replace(claimed, run_id=GraphRunId("other-run"))

    with pytest.raises(SnapshotMismatchError, match="scope-run coordinate"):
        await executor.execute(prepared.claim, wrong_run)

    session = await executor.execute(prepared.claim, claimed)
    await session.aclose()


async def test_typed_failure_returns_awaiting_resume_without_retry() -> None:
    calls = 0

    async def fail(values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.failure(f"failed:{values['value']}")

    graph = graph_with_nodes(node("a", fail))
    executor = GraphExecutor(graph)
    state, results = await run_frontier(executor, graph, started(graph), "input")
    assert isinstance(results[0], TaskFailure)
    disposition = await executor.prepare(string_request(graph, state, "input"))
    assert disposition == AwaitingResume((GraphNodeId("a"),), ())
    assert calls == 1


async def test_ordinary_exception_leaves_pending_node_for_exact_fence() -> None:
    async def explode(values: Graph.Values[str]) -> Graph.Values[str]:
        raise RuntimeError(values["value"])

    graph = graph_with_nodes(node("a", explode))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "boom")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    with pytest.raises(RuntimeError, match="boom"):
        await session.next(claimed)
    await session.aclose()
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))
    settlement = fenced.frontier.nodes[0].settlement
    assert isinstance(settlement, PendingGraphNode)
    assert settlement == PendingGraphNode(settlement.input)


async def test_claim_is_one_shot_and_bound_to_committed_state() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    with pytest.raises(ResultCollectionError, match="committed"):
        await executor.execute(prepared.claim, initial)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    await session.aclose()
    with pytest.raises(ResultCollectionError, match="already"):
        await executor.execute(prepared.claim, claimed)


async def test_claim_rejects_a_forged_prepared_task_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = graph_with_nodes(node("a"))
    initial = started(graph)
    original_plan = frontier_module.plan_tasks

    def forge_task_identity(
        graph: CompiledGraph[str],
        state: GraphRunState,
        limits: ExecutionLimits,
    ) -> tuple[GraphTask, ...]:
        tasks = original_plan(graph, state, limits)
        return (replace(tasks[0], task_id=TaskId("forged")),)

    monkeypatch.setattr(frontier_module, "plan_tasks", forge_task_identity)
    executor = GraphExecutor(graph)
    prepared = await executor.prepare(string_request(graph, initial, "input"))
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(prepared.claim, claimed)
    assert not prepared.claim.consumed


async def test_concurrent_consumers_of_one_prepared_claim_have_exactly_one_winner() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)

    outcomes = await asyncio.gather(
        executor.execute(prepared.claim, claimed),
        executor.execute(prepared.claim, claimed),
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
    parent = started(graph)
    activation = ParentGraphActivation(parent.run_id, parent.superstep, GraphNodeId("nested"))
    missing = await executor.prepare(string_request(graph, parent, "input", (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    assert missing.missing == (MissingChild(activation),)
    assert missing.active == ()
    active = await executor.prepare(string_request(graph, parent, "input", (ActiveChild(activation),)))
    assert isinstance(active, WaitingForChildren)
    assert active.missing == ()
    assert active.active == (ActiveChild(activation),)


async def test_completed_nested_child_is_a_precomputed_completion_on_the_same_path() -> None:
    graph = nested_graph()
    executor = GraphExecutor(graph)
    parent = started(graph)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(string_request(graph, parent, "input", (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    child_graph = graph.nested_graphs[GraphNodeId("nested")]
    projection = CompletedChild(
        activation,
        child_output(child_graph, "child-output"),
    )
    execution_request = string_request(graph, parent, "input", (projection,))
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    resources = prepared.claim.command.resources
    assert resources is not None
    assert tuple(item.node_id for item in resources.acquisitions) == (GraphNodeId("ordinary"),)
    claimed = reduce_graph_run(parent, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        result = await session.next(claimed)
        assert isinstance(result.result, TaskSuccess)
        assert output_value(result.result) == "child-output"
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
    parent = started(graph)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(string_request(graph, parent, "input", (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    projection = AbortedChild(activation, GraphAbortReason("child aborted"))
    execution_request = string_request(graph, parent, "input", (projection,))
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(parent, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        result = await session.next(claimed)
        assert isinstance(result.result, TaskFailure)
        assert result.result.failure == "child aborted"
    finally:
        await session.aclose()


async def test_nested_projection_requires_terminal_child_state() -> None:
    graph = nested_graph()
    parent = started(graph)
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    (owner, _evidence_reader) = await fresh_root(
        graph,
        root_scope_run(GraphRunId("projection-owner")),
        admit_graph_input(graph, Graph.values(value="input")),
        ExecutionLimits(),
        None,
    )

    with pytest.raises(ResultCollectionError, match="not terminal"):
        owner.terminal_projection(activation)


async def test_concurrent_runs_share_executor_without_cross_run_state() -> None:
    barrier = asyncio.Barrier(2)

    async def execute(values: Graph.Values[str]) -> Graph.Values[str]:
        await barrier.wait()
        return values

    graph = graph_with_nodes(node("a", execute))
    executor = GraphExecutor(graph)
    first, second = started(graph, "first"), started(graph, "second")
    first_request = string_request(graph, first, "first")
    second_request = string_request(graph, second, "second")
    first_p, second_p = await asyncio.gather(
        executor.prepare(first_request),
        executor.prepare(second_request),
    )
    assert isinstance(first_p, ExecutableFrontier) and isinstance(second_p, ExecutableFrontier)
    first_claimed = reduce_graph_run(first, first_p.claim.command)
    second_claimed = reduce_graph_run(second, second_p.claim.command)
    first_session, second_session = await asyncio.gather(
        executor.execute(first_p.claim, first_claimed),
        executor.execute(second_p.claim, second_claimed),
    )
    try:
        one, two = await asyncio.gather(
            first_session.next(first_claimed),
            second_session.next(second_claimed),
        )
        assert isinstance(one.result, TaskSuccess)
        assert isinstance(two.result, TaskSuccess)
        assert output_value(one.result) == "first"
        assert output_value(two.result) == "second"
        assert one.result.task.task_id != two.result.task.task_id
    finally:
        await asyncio.gather(first_session.aclose(), second_session.aclose())


async def test_context_and_input_identity_are_isolated_per_task() -> None:
    trace = ContextVar("trace", default="missing")

    async def read(values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value=f"{trace.get()}:{values['value']}")

    graph = graph_with_nodes(node("a", read), node("b", read))
    token = trace.set("caller")
    try:
        executor = GraphExecutor(graph)
        state = started(graph)
        execution_request = string_request(graph, state, "input")
        prepared = await executor.prepare(execution_request)
        assert isinstance(prepared, ExecutableFrontier)
        claimed = reduce_graph_run(state, prepared.claim.command)
        session = await executor.execute(prepared.claim, claimed)
        try:
            first = await session.next(claimed)
            assert isinstance(first.result, TaskSuccess)
            assert output_value(first.result) == "caller:input"
        finally:
            await session.aclose()
        assert trace.get() == "caller"
    finally:
        trace.reset(token)


async def test_node_output_contract_error_is_not_forged_into_settlement() -> None:
    async def invalid(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(other="wrong-port")

    graph = graph_with_nodes(node("a", invalid))
    executor = GraphExecutor(graph)
    state = started(graph)
    execution_request = string_request(graph, state, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        with pytest.raises(GraphValueAdmissionError, match="names do not match"):
            await session.next(claimed)
    finally:
        await session.aclose()


async def test_node_contract_error_is_not_forged_into_settlement() -> None:
    async def invalid(_values: Graph.Values[str]) -> bytes:
        return b"unsupported"

    graph = graph_with_nodes(node("a", cast(NodeCallable[str], invalid)))
    executor = GraphExecutor(graph)
    state = started(graph)
    execution_request = string_request(graph, state, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(state, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        with pytest.raises(NodeExecutionContractError, match="unsupported outcome"):
            await session.next(claimed)
    finally:
        await session.aclose()

    assert isinstance(claimed.frontier.nodes[0].settlement, PendingGraphNode)
    assert claimed.execution is not None


async def test_prepare_reports_terminal_and_settled_dispositions_without_claiming() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        result = await session.next(claimed)
        settled = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()
    ready = await executor.prepare(replace(execution_request, state=settled))
    assert isinstance(ready, ReadyToResolve)
    completed = reduce_graph_run(settled, ready.command)
    assert completed.status is GraphRunStatus.COMPLETED
    terminal = await executor.prepare(replace(execution_request, state=completed))
    assert isinstance(terminal, CompletedGraph)

    aborted = reduce_graph_run(initial, AbortGraphRun(initial.revision, GraphAbortReason("operator")))
    aborted_disposition = await executor.prepare(replace(execution_request, state=aborted))
    assert isinstance(aborted_disposition, AbortedGraph)


async def test_prepare_rejects_reentry_into_an_active_execution() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    with pytest.raises(ResultCollectionError, match="original execution session"):
        await executor.prepare(replace(execution_request, state=claimed))


async def test_resume_projection_validates_each_action_variant_and_lifecycle() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    graph = graph_with_nodes(node("a", fail))
    executor = GraphExecutor(graph)
    state, _results = await run_frontier(executor, graph, started(graph), "input")
    execution_request = string_request(graph, state, "input")
    with pytest.raises(SnapshotMismatchError, match="non-empty"):
        prepare_resume(graph, ResumeRequest(state, execution_request.scope_run, execution_request.frames, ()))
    duplicate = ResumeFailedNodeRequest(
        (),
        GraphNodeId("a"),
        OverrideNodeInput(Graph.values(value="one")),
    )
    with pytest.raises(SnapshotMismatchError, match="distinct"):
        prepare_resume(
            graph,
            ResumeRequest(
                state,
                execution_request.scope_run,
                execution_request.frames,
                (duplicate, duplicate),
            ),
        )
    with pytest.raises(SnapshotMismatchError, match="unknown frontier"):
        prepare_resume(
            graph,
            ResumeRequest(
                state,
                execution_request.scope_run,
                execution_request.frames,
                (
                    ResumeFailedNodeRequest(
                        (),
                        GraphNodeId("missing"),
                        OverrideNodeInput(Graph.values(value="retry")),
                    ),
                ),
            ),
        )
    with pytest.raises(SnapshotMismatchError, match="scoped"):
        prepare_resume(
            graph,
            ResumeRequest(
                state,
                execution_request.scope_run,
                execution_request.frames,
                (
                    ResumeFailedNodeRequest(
                        (GraphNodeId("foreign"),),
                        GraphNodeId("a"),
                        OverrideNodeInput(Graph.values(value="retry")),
                    ),
                ),
            ),
        )


async def test_executor_rejects_graph_ownership_and_parent_shape_mismatches() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    foreign = replace(initial, definition_id=GraphDefinitionId("foreign.graph"))
    with pytest.raises(SnapshotMismatchError, match="compiled graph identity"):
        await executor.prepare(string_request(graph, foreign, "input"))

    parent = ParentGraphActivation(GraphRunId("parent"), 0, GraphNodeId("nested"))
    root_with_parent = replace(
        initial,
        run_id=child_graph_run_id(parent.run_id, parent.superstep, parent.node_id),
        parent=parent,
    )
    with pytest.raises(SnapshotMismatchError, match="root graph"):
        await executor.prepare(string_request(graph, root_with_parent, "input"))

    parent_graph = nested_graph()
    child_graph = parent_graph.nested_graphs[GraphNodeId("nested")]
    child_executor = GraphExecutor(child_graph)
    child_without_parent = reduce_graph_run(
        None,
        project_start_graph_command(child_graph, GraphRunId("child-run")),
    )
    child_scope = ScopeRunCoordinate((GraphNodeId("nested"),), child_without_parent.run_id)
    with pytest.raises(SnapshotMismatchError, match="nested graph"):
        await child_executor.prepare(
            string_request(
                child_graph,
                child_without_parent,
                "input",
                scope_run=child_scope,
            )
        )

    shared_child = child_definition("shared.child")
    shared_parent = GraphDefinition(
        definition_id=GraphDefinitionId("shared.parent"),
        version=GraphDefinitionVersion(1),
        nodes=(nested_node("first", shared_child), nested_node("second", shared_child)),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    GraphExecutor(compile_graph(shared_parent))


async def test_resume_rejects_non_failed_and_invalid_input_variants() -> None:
    graph = graph_with_nodes(node("a"))
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    with pytest.raises(SnapshotMismatchError, match="failure resume"):
        prepare_resume(
            graph,
            ResumeRequest(
                initial,
                execution_request.scope_run,
                execution_request.frames,
                (
                    ResumeFailedNodeRequest(
                        (),
                        GraphNodeId("a"),
                        OverrideNodeInput(Graph.values(value="x")),
                    ),
                ),
            ),
        )
    with pytest.raises(SnapshotMismatchError, match="interrupt resume"):
        prepare_resume(
            graph,
            ResumeRequest(
                initial,
                execution_request.scope_run,
                execution_request.frames,
                (
                    ResumeInterruptedNodeRequest(
                        (),
                        GraphNodeId("a"),
                        GraphInterruptId("interrupt"),
                        OverrideNodeInput(Graph.values(value="x")),
                    ),
                ),
            ),
        )
    with pytest.raises(SnapshotMismatchError, match="skip"):
        prepare_resume(
            graph,
            ResumeRequest(
                initial,
                execution_request.scope_run,
                execution_request.frames,
                (SkipFailedNodeRequest((), GraphNodeId("a"), "skip", None),),
            ),
        )


async def test_resume_projection_covers_override_default_skip_and_interrupt_input_guards() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    codec = _Codec()
    resumable = graph_with_nodes(
        node("a", fail),
        definition_id="resumable.graph",
        resume_input=ResumeInputBinding(
            GraphResumeInputCodecId("input.v1"),
            1,
            codec,
            codec,
        ),
    )
    executor = GraphExecutor(resumable)
    initial = started(resumable)
    execution_request = string_request(resumable, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    with pytest.raises(SnapshotMismatchError, match="quiescent"):
        prepare_resume(
            resumable,
            ResumeRequest(
                claimed,
                execution_request.scope_run,
                execution_request.frames,
                (),
            ),
        )
    session = await executor.execute(prepared.claim, claimed)
    try:
        result = await session.next(claimed)
        failed = reduce_graph_run(claimed, result.command)
    finally:
        await session.aclose()

    failed_request = replace(execution_request, state=failed)
    override = prepare_resume(
        resumable,
        ResumeRequest(
            failed,
            failed_request.scope_run,
            failed_request.frames,
            (
                ResumeFailedNodeRequest(
                    (),
                    GraphNodeId("a"),
                    OverrideNodeInput(Graph.values(value="retry")),
                ),
            ),
        ),
    )
    assert override.command.actions[0].node_id == GraphNodeId("a")
    assert len(override.inputs) == 1
    default = prepare_resume(
        resumable,
        ResumeRequest(
            failed,
            failed_request.scope_run,
            failed_request.frames,
            (ResumeFailedNodeRequest((), GraphNodeId("a"), UseMaterializedInput()),),
        ),
    )
    assert default.command.actions[0].node_id == GraphNodeId("a")
    assert len(default.inputs) == 1
    skip = prepare_resume(
        resumable,
        ResumeRequest(
            failed,
            failed_request.scope_run,
            failed_request.frames,
            (SkipFailedNodeRequest((), GraphNodeId("a"), "operator", None),),
        ),
    )
    assert skip.command.actions[0].node_id == GraphNodeId("a")
    assert skip.inputs == ()

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    interrupted_graph = graph_with_nodes(
        node("a", interrupt),
        definition_id="resumable.interrupt",
        resume_input=ResumeInputBinding(
            GraphResumeInputCodecId("input.v1"),
            1,
            codec,
            codec,
        ),
    )
    interrupt_executor = GraphExecutor(interrupted_graph)
    interrupt_initial = started(interrupted_graph)
    interrupt_request = string_request(interrupted_graph, interrupt_initial, "input")
    interrupt_prepared = await interrupt_executor.prepare(interrupt_request)
    assert isinstance(interrupt_prepared, ExecutableFrontier)
    interrupt_claimed = reduce_graph_run(interrupt_initial, interrupt_prepared.claim.command)
    interrupt_session = await interrupt_executor.execute(
        interrupt_prepared.claim,
        interrupt_claimed,
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
    prepared_interrupt = prepare_resume(
        interrupted_graph,
        ResumeRequest(
            interrupted,
            interrupt_request.scope_run,
            interrupt_request.frames,
            (
                ResumeInterruptedNodeRequest(
                    (),
                    GraphNodeId("a"),
                    interrupt_id,
                    OverrideNodeInput(Graph.values(value="answer")),
                ),
            ),
        ),
    )
    assert len(prepared_interrupt.inputs) == 1


async def test_override_resume_admission_preserves_validation_and_codec_order() -> None:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("failed")

    codec = _TrackingCodec()
    graph = graph_with_nodes(
        node("a", fail),
        definition_id="resume.admission.order",
        resume_input=ResumeInputBinding(
            GraphResumeInputCodecId("input.v1"),
            1,
            codec,
            codec,
        ),
    )
    executor = GraphExecutor(graph)
    initial = started(graph)
    initial_request = string_request(graph, initial, "input")
    with pytest.raises(SnapshotMismatchError, match="failure resume"):
        prepare_resume(
            graph,
            ResumeRequest(
                initial,
                initial_request.scope_run,
                initial_request.frames,
                (
                    ResumeFailedNodeRequest(
                        (),
                        GraphNodeId("a"),
                        OverrideNodeInput(Graph.values(value="retry")),
                    ),
                ),
            ),
        )
    assert codec.events == []

    failed, _results = await run_frontier(executor, graph, initial, "input")
    failed_request = string_request(graph, failed, "input")
    failed_resume_request = ResumeRequest(
        failed,
        failed_request.scope_run,
        failed_request.frames,
        (
            ResumeFailedNodeRequest(
                (),
                GraphNodeId("a"),
                OverrideNodeInput(Graph.values(value="retry")),
            ),
        ),
    )
    codec.events.clear()
    failed_prepared = prepare_resume(graph, failed_resume_request)
    assert codec.events == ["encode", "decode"]
    assert failed_prepared.command.actions[0].node_id == GraphNodeId("a")
    assert len(failed_prepared.inputs) == 1
    assert failed_prepared.inputs[0].frame.entries[0] == NamedValue("value", "retry")
    resumed = reduce_graph_run(failed, failed_prepared.command)
    assert isinstance(resumed.frontier.nodes[0].settlement, PendingGraphNode)

    codec.tamper = "name"
    codec.events.clear()
    tampered_name_request = replace(failed_resume_request)
    name_state = tampered_name_request.state
    name_frames = tampered_name_request.frames
    with pytest.raises(GraphValueAdmissionError) as name_error:
        prepare_resume(graph, tampered_name_request)
    assert str(name_error.value) == (
        "node input names do not match the compiled descriptor: expected ('value',), got ('other',)"
    )
    assert name_error.value.__cause__ is None
    assert codec.events == ["encode", "decode"]
    assert tampered_name_request.state is name_state
    assert tampered_name_request.frames is name_frames

    codec.tamper = "type"
    codec.events.clear()
    tampered_type_request = replace(failed_resume_request)
    type_state = tampered_type_request.state
    type_frames = tampered_type_request.frames
    with pytest.raises(GraphValueAdmissionError) as type_error:
        prepare_resume(graph, tampered_type_request)
    assert str(type_error.value) == "node input value for 'value' does not have its exact declared type"
    assert type_error.value.__cause__ is None
    assert codec.events == ["encode", "decode"]
    assert tampered_type_request.state is type_state
    assert tampered_type_request.frames is type_frames

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"question")

    interrupt_codec = _TrackingCodec()
    interrupted_graph = graph_with_nodes(
        node("a", interrupt),
        definition_id="resume.admission.interrupt.order",
        resume_input=ResumeInputBinding(
            GraphResumeInputCodecId("input.v1"),
            1,
            interrupt_codec,
            interrupt_codec,
        ),
    )
    interrupt_executor = GraphExecutor(interrupted_graph)
    interrupt_initial = started(interrupted_graph)
    interrupted, _results = await run_frontier(interrupt_executor, interrupted_graph, interrupt_initial, "input")
    interrupt_request = string_request(interrupted_graph, interrupted, "input")
    interrupt_settlement = interrupted.frontier.nodes[0].settlement
    assert isinstance(interrupt_settlement, InterruptedGraphNode)
    identity = interrupt_settlement.interrupt.identity
    stale_request = ResumeRequest(
        interrupted,
        interrupt_request.scope_run,
        interrupt_request.frames,
        (
            ResumeInterruptedNodeRequest(
                (),
                GraphNodeId("a"),
                GraphInterruptId("stale"),
                OverrideNodeInput(Graph.values(value="answer")),
            ),
        ),
    )
    with pytest.raises(SnapshotMismatchError, match="does not match"):
        prepare_resume(interrupted_graph, stale_request)
    assert interrupt_codec.events == []

    interrupt_id = graph_interrupt_id(
        identity.run_id,
        identity.superstep,
        identity.node_id,
        identity.execution_generation,
    )
    interrupt_codec.events.clear()
    interrupt_prepared = prepare_resume(
        interrupted_graph,
        ResumeRequest(
            interrupted,
            interrupt_request.scope_run,
            interrupt_request.frames,
            (
                ResumeInterruptedNodeRequest(
                    (),
                    GraphNodeId("a"),
                    interrupt_id,
                    OverrideNodeInput(Graph.values(value="answer")),
                ),
            ),
        ),
    )
    assert interrupt_codec.events == ["encode", "decode"]
    assert len(interrupt_prepared.inputs) == 1
    assert interrupt_prepared.inputs[0].frame.entries[0] == NamedValue("value", "answer")


async def test_prepare_rejects_an_empty_resource_admission_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = ResourceId("file")
    graph = graph_with_nodes(
        node("a", resources=(resource,)),
        resources=(ResourceDefinition(resource, 0),),
        definition_id="resource.empty-admission",
    )

    def empty_admission(
        _graph: CompiledGraph[str],
        _tasks: tuple[GraphTask, ...],
        snapshot: ResourceSnapshot,
    ) -> TaskAdmission:
        return TaskAdmission(snapshot, (), ())

    monkeypatch.setattr(admission_module, "admit_tasks", empty_admission)
    executor = GraphExecutor(graph)
    state = started(graph)
    with pytest.raises(ResultCollectionError, match="did not create acquisition"):
        await executor.prepare(string_request(graph, state, "input"))


async def test_nested_conditional_source_is_rejected_at_compile_time() -> None:
    child = child_definition("nested.error.child")
    with pytest.raises(GraphValidationError, match=r"nested.*conditional"):
        graph_with_nodes(
            nested_node("nested", child),
            edges=(ConditionalEdge(GraphNodeId("nested"), GraphRouteId("done"), END),),
            definition_id="nested.error.parent",
        )


async def test_nested_invalid_completion_enters_error_draining() -> None:
    calls = 0
    commits: list[Graph.Transition[str]] = []

    async def leaf(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return values

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        commits.append(transition)
        return transition.candidate_state

    child = Graph[str]("nested.invalid-completion.child")
    child.add_node(
        "leaf",
        leaf,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    child.set_outputs({"value": Graph.node_output("leaf", "value")})
    parent = Graph[str]("nested.invalid-completion.parent")
    parent.add_node(
        "nested",
        child,
        inputs={"value": Graph.graph_input("value", str)},
    )
    parent.add_conditional_edge("nested", "done", Graph.END)
    parent.set_outputs({})

    with pytest.raises(GraphValidationError, match=r"nested.*conditional"):
        await parent.run(Graph.values(value="input"), commit=commit)

    assert calls == 0
    assert commits == []


async def test_prepared_claim_remains_bound_to_executor_and_prepared_input() -> None:
    calls = 0

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return values

    graph = graph_with_nodes(node("a", operation))
    owner = GraphExecutor(graph)
    other = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await owner.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await other.execute(prepared.claim, claimed)
    assert not prepared.claim.consumed
    assert calls == 0

    session = await owner.execute(prepared.claim, claimed)
    try:
        completed = await session.next(claimed)
        assert isinstance(completed.result, TaskSuccess)
        assert output_value(completed.result) == "input"
    finally:
        await session.aclose()
    assert prepared.claim.consumed
    assert calls == 1


async def test_fenced_unstarted_claim_cannot_start_or_be_consumed() -> None:
    calls = 0

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return values

    graph = graph_with_nodes(node("a", operation))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    assert claimed.execution is not None
    fenced = reduce_graph_run(claimed, FenceGraphExecution(claimed.revision, claimed.execution.token))

    with pytest.raises(ResultCollectionError, match="committed graph state"):
        await executor.execute(prepared.claim, fenced)
    assert not prepared.claim.consumed
    assert calls == 0


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    value: str


PipelineValue: TypeAlias = InputSnapshot


async def test_parallel_context_mutations_are_isolated_and_request_input_is_frozen() -> None:
    trace = ContextVar("parallel-trace", default="missing")
    barrier = asyncio.Barrier(2)
    observed: list[InputSnapshot] = []

    def definition(name: str) -> CallableNodeDefinition[PipelineValue]:
        async def operation(
            values: Graph.Values[PipelineValue],
        ) -> Graph.Values[PipelineValue]:
            node_input = values["value"]
            assert trace.get() == "caller"
            observed.append(node_input)
            trace.set(name)
            await asyncio.wait_for(barrier.wait(), timeout=1)
            return Graph.values(value=InputSnapshot(f"{trace.get()}:{node_input.value}"))

        return CallableNodeDefinition(
            GraphNodeId(name),
            operation,
            normalize_input_bindings({"value": Graph.graph_input("value", InputSnapshot)}),
            normalize_output_declarations({"value": InputSnapshot}),
        )

    graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("context.graph"),
            version=GraphDefinitionVersion(1),
            nodes=(definition("a"), definition("b")),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    executor = GraphExecutor(graph)
    initial = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("context-run")))
    node_input = InputSnapshot("input")
    execution_request = request_with_values(
        graph,
        initial,
        root_scope_run(initial.run_id),
        Graph.values(value=node_input),
    )
    token = trace.set("caller")
    try:
        prepared = await executor.prepare(execution_request)
        assert isinstance(prepared, ExecutableFrontier)
        current = reduce_graph_run(initial, prepared.claim.command)
        session = await executor.execute(prepared.claim, current)
        outputs: list[str] = []
        try:
            for _ in range(2):
                completed = await session.next(current)
                assert isinstance(completed.result, TaskSuccess)
                outputs.append(_frame_value(completed.result.output, "value").value)
                current = reduce_graph_run(current, completed.command)
        finally:
            await session.aclose()
        assert trace.get() == "caller"
    finally:
        trace.reset(token)

    assert sorted(outputs) == ["a:input", "b:input"]
    assert len(observed) == 2 and all(item is node_input for item in observed)
    assert observed == [node_input, node_input]


async def test_plain_values_use_the_normal_completion_path() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    current = started(graph)
    settled, results = await run_frontier(executor, graph, current, "input")
    assert len(results) == 1 and isinstance(results[0], TaskSuccess)
    assert output_value(results[0]) == "input"
    assert settled.execution is None


async def test_node_success_subclass_uses_the_normal_completion_path() -> None:
    async def explicit_success(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(values)

    graph = graph_with_nodes(node("a", explicit_success))
    executor = GraphExecutor(graph)
    settled, results = await run_frontier(executor, graph, started(graph), "input")

    assert len(results) == 1 and isinstance(results[0], TaskSuccess)
    assert output_value(results[0]) == "input"
    assert settled.execution is None


async def test_nested_graph_can_prepare_a_grandchild_with_exact_parent_coordinates() -> None:
    leaf = child_definition("leaf.graph")
    child = GraphDefinition(
        definition_id=GraphDefinitionId("grandchild.parent"),
        version=GraphDefinitionVersion(1),
        nodes=(nested_node("child", leaf),),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({"value": Graph.node_output("child", "value")}),
    )
    root = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("grandchild.root"),
            version=GraphDefinitionVersion(1),
            nodes=(nested_node("root", child),),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    root_executor = GraphExecutor(root)
    root_state = reduce_graph_run(None, project_start_graph_command(root, GraphRunId("nested-run")))
    root_scope = root_scope_run(root_state.run_id)
    child_activation = ParentGraphActivation(root_state.run_id, 0, GraphNodeId("root"))
    child_wait = await root_executor.prepare(
        string_request(root, root_state, "input", (MissingChild(child_activation),))
    )
    assert isinstance(child_wait, WaitingForChildren)
    assert child_wait.missing == (MissingChild(child_activation),)
    _activation, child_graph, child_scope, child_state = started_nested_child(
        root,
        root_state,
        root_scope,
        GraphNodeId("root"),
    )
    child_executor = GraphExecutor(child_graph)
    grandchild_activation = ParentGraphActivation(child_state.run_id, 0, GraphNodeId("child"))

    grandchild_wait = await child_executor.prepare(
        string_request(
            child_graph,
            child_state,
            "input",
            (MissingChild(grandchild_activation),),
            scope_run=child_scope,
        )
    )
    assert isinstance(grandchild_wait, WaitingForChildren)
    assert grandchild_wait.missing == (MissingChild(grandchild_activation),)
    grandchild_graph = child_graph.nested_graphs[GraphNodeId("child")]
    grandchild_scope = child_scope_run_for_activation(child_scope, grandchild_activation)
    grandchild_command = project_start_graph_command(
        grandchild_graph,
        grandchild_scope.graph_run_id,
        grandchild_activation,
    )
    assert grandchild_command.parent == grandchild_activation
    assert grandchild_command.run_id == child_graph_run_id(
        child_state.run_id,
        child_state.superstep,
        GraphNodeId("child"),
    )


async def test_nested_child_start_preserves_all_canonical_entry_nodes() -> None:
    child = GraphDefinition(
        definition_id=GraphDefinitionId("entries.child"),
        version=GraphDefinitionVersion(1),
        nodes=tuple(node(node_id) for node_id in ("c", "a", "b")),
        edges=(),
        entries=(),
        outputs=normalize_graph_output_declarations({}),
    )
    graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("entries.parent"),
            version=GraphDefinitionVersion(1),
            nodes=(nested_node("nested", child),),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("entry-run")))
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("nested"))
    missing = await executor.prepare(string_request(graph, parent, "input", (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    _activation, _child_graph, _coordinate, child_state = started_nested_child(
        graph,
        parent,
        root_scope_run(parent.run_id),
        GraphNodeId("nested"),
    )
    assert tuple(item.node_id for item in child_state.frontier.nodes) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
        GraphNodeId("c"),
    )


async def test_nested_completion_contributes_to_a_cross_superstep_join() -> None:
    child = child_definition("join.child")
    graph = compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("join.parent"),
            version=GraphDefinitionVersion(1),
            nodes=(nested_node("a", child), node("b"), node("joined")),
            edges=(
                DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
                JoinEdge((GraphNodeId("a"), GraphNodeId("b")), GraphNodeId("joined")),
            ),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
        )
    )
    executor = GraphExecutor(graph)
    parent = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("join-run")))
    activation = ParentGraphActivation(parent.run_id, 0, GraphNodeId("a"))
    missing = await executor.prepare(string_request(graph, parent, "input", (MissingChild(activation),)))
    assert isinstance(missing, WaitingForChildren)
    child_graph = graph.nested_graphs[GraphNodeId("a")]
    projection = CompletedChild(
        activation,
        child_output(child_graph, "child-output"),
    )

    after_child = await run_and_resolve(executor, graph, parent, "input", (projection,))
    assert tuple(item.node_id for item in after_child.frontier.nodes) == (GraphNodeId("b"),)
    assert after_child.join_progress == (
        GraphJoinProgress(
            (GraphNodeId("a"), GraphNodeId("b")),
            GraphNodeId("joined"),
            frozenset({GraphNodeId("a")}),
        ),
    )

    after_b = await run_and_resolve(executor, graph, after_child, "input")
    assert tuple(item.node_id for item in after_b.frontier.nodes) == (GraphNodeId("joined"),)
    assert after_b.join_progress == ()


async def test_execution_uses_canonical_node_order_for_different_lengths() -> None:
    barrier = asyncio.Barrier(2)

    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        await asyncio.wait_for(barrier.wait(), timeout=1)
        return values

    graph = graph_with_nodes(node("aa", operation), node("z", operation))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)
    try:
        first = await session.next(claimed)
        after_first = reduce_graph_run(claimed, first.command)
        second = await session.next(after_first)
    finally:
        await session.aclose()

    assert (first.result.task.node_id, second.result.task.node_id) == (
        GraphNodeId("aa"),
        GraphNodeId("z"),
    )


async def test_late_settlement_cannot_overwrite_a_reclaimed_generation() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    first = await executor.prepare(execution_request)
    assert isinstance(first, ExecutableFrontier)
    first_state = reduce_graph_run(initial, first.claim.command)
    first_session = await executor.execute(first.claim, first_state)
    late = await first_session.next(first_state)
    await first_session.aclose()
    assert first_state.execution is not None
    fenced = reduce_graph_run(
        first_state,
        FenceGraphExecution(first_state.revision, first_state.execution.token),
    )
    second_request = replace(execution_request, state=fenced)
    second = await executor.prepare(second_request)
    assert isinstance(second, ExecutableFrontier)
    second_state = reduce_graph_run(fenced, second.claim.command)

    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(second_state, late.command)
    assert second_state.execution is not None
    assert second_state.execution.token.generation == 2


async def test_node_initiated_cancellation_waits_for_sibling_cleanup() -> None:
    sibling_started = asyncio.Event()
    sibling_cleaned = asyncio.Event()

    async def sibling(values: Graph.Values[str]) -> Graph.Values[str]:
        sibling_started.set()
        try:
            await asyncio.sleep(10)
        finally:
            sibling_cleaned.set()
        return values

    async def cancel(_values: Graph.Values[str]) -> Graph.Values[str]:
        await sibling_started.wait()
        raise asyncio.CancelledError

    graph = graph_with_nodes(node("a", sibling), node("b", cancel))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
    assert isinstance(prepared, ExecutableFrontier)
    claimed = reduce_graph_run(initial, prepared.claim.command)
    session = await executor.execute(prepared.claim, claimed)

    with pytest.raises(asyncio.CancelledError):
        await session.next(claimed)

    assert sibling_cleaned.is_set()
    assert session.quiescent
    assert claimed.execution is not None


async def test_claim_guard_rejects_a_forged_committed_attempt_token() -> None:
    graph = graph_with_nodes(node("a"))
    executor = GraphExecutor(graph)
    initial = started(graph)
    execution_request = string_request(graph, initial, "input")
    prepared = await executor.prepare(execution_request)
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
        await executor.execute(prepared.claim, forged)
    assert not prepared.claim.consumed

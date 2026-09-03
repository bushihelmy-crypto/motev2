import asyncio
from dataclasses import replace

import pytest
from tests.execution.driver import step_request

from mote_kernel.execution import Graph
from mote_kernel.execution.engine.snapshot_guard import require_snapshot_matches_graph
from mote_kernel.execution.engine.superstep import ExecutableFrontier
from mote_kernel.execution.errors import InvalidExecutionSnapshotError
from mote_kernel.execution.executor import GraphExecutor
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.definition import GraphDefinition
from mote_kernel.execution.graph.node import CallableNodeDefinition, NodeCallable
from mote_kernel.execution.graph.ports import (
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.topology import CompiledGraph
from mote_kernel.execution.graph_run import project_start_graph_command
from mote_kernel.execution.resource import ResourceDefinition
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    ClaimGraphExecution,
    FenceGraphExecution,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRunId,
    GraphStateTransitionError,
    PendingGraphNode,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    SettleGraphNode,
    SucceededGraphNode,
    reduce_graph_run,
)

pytestmark = pytest.mark.asyncio


class Codec:
    def encode(self, value: Graph.Values[str]) -> bytes:
        return value["value"].encode()

    def decode(self, payload: bytes) -> Graph.Values[str]:
        return Graph.values(value=payload.decode())


class CommitLog:
    def __init__(self) -> None:
        self.transitions: list[Graph.Transition[str]] = []

    async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
        self.transitions.append(transition)
        return transition.candidate_state


def resource_graph(
    operations: tuple[tuple[str, NodeCallable[str], tuple[str, ...]], ...],
    *,
    definition_id: str = "resource.graph",
    codec: Codec | None = None,
) -> Graph[str]:
    graph = Graph[str](definition_id)
    if codec is not None:
        graph.set_resume_codec("resource.input.v1", 1, codec.encode, codec.decode)
    for node_id, operation, resources in operations:
        graph.add_node(
            node_id,
            operation,
            inputs={"value": Graph.graph_input("value", str)},
            outputs={"value": str},
            resources=resources,
        )
    graph.set_outputs({})
    return graph


async def echo(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


def root_claims(commits: CommitLog) -> tuple[ClaimGraphExecution, ...]:
    return tuple(
        transition.command
        for transition in commits.transitions
        if transition.scope == () and isinstance(transition.command, ClaimGraphExecution)
    )


def root_settlements(commits: CommitLog) -> tuple[Graph.Transition[str], ...]:
    return tuple(
        transition
        for transition in commits.transitions
        if transition.scope == () and isinstance(transition.command, SettleGraphNode)
    )


def child_graph(operation: NodeCallable[str] = echo) -> Graph[str]:
    child = Graph[str]("resource.child")
    child.add_node(
        "child",
        operation,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    child.set_outputs({"value": Graph.node_output("child", "value")})
    return child


def nested_resource_graph(operation: NodeCallable[str] = echo) -> Graph[str]:
    parent = Graph[str]("resource.nested")
    parent.add_node(
        "nested",
        child_graph(),
        inputs={"value": Graph.graph_input("value", str)},
    )
    parent.add_node(
        "resource",
        operation,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
        resources=("file",),
    )
    parent.set_outputs({})
    return parent


def internal_node(
    node_id: str,
    resources: tuple[ResourceId, ...],
) -> CallableNodeDefinition[str]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        echo,
        normalize_input_bindings({"value": Graph.graph_input("value", str)}),
        normalize_output_declarations({"value": str}),
        resources,
    )


def internal_resource_graph(
    *,
    resource_order: tuple[ResourceId, ...],
    requirement: tuple[ResourceId, ...],
) -> CompiledGraph[str]:
    return compile_graph(
        GraphDefinition(
            definition_id=GraphDefinitionId("resource.snapshot"),
            version=GraphDefinitionVersion(1),
            nodes=(internal_node("a", requirement),),
            edges=(),
            entries=(),
            outputs=normalize_graph_output_declarations({}),
            resources=tuple(ResourceDefinition(resource_id) for resource_id in resource_order),
        )
    )


def claimed_internal_state(
    graph: CompiledGraph[str],
) -> tuple[GraphExecutor[str], Graph.State, ExecutableFrontier[str]]:
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    prepared = executor.prepare(step_request(graph, state, "input").execution_request())
    assert isinstance(prepared, ExecutableFrontier)
    return executor, reduce_graph_run(state, prepared.claim.command), prepared


async def test_claim_admits_all_resource_nodes_once() -> None:
    graph = resource_graph(
        (
            ("a", echo, ("file",)),
            ("b", echo, ("file",)),
        )
    )
    commits = CommitLog()

    result = await graph.run(Graph.values(value="input"), commit=commits)

    assert isinstance(result, Graph.CompletedResult)
    claims = root_claims(commits)
    assert len(claims) == 1
    resources = claims[0].resources
    assert resources is not None
    assert tuple(item.node_id for item in resources.acquisitions) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
    )


async def test_release_and_waiter_progress_are_authoritative_before_next_selection() -> None:
    starts: list[str] = []

    def operation(node_id: str) -> NodeCallable[str]:
        async def run(values: Graph.Values[str]) -> Graph.Values[str]:
            starts.append(node_id)
            return values

        return run

    graph = resource_graph(
        (
            ("a", operation("a"), ("file",)),
            ("b", operation("b"), ("file",)),
        )
    )
    commits = CommitLog()
    await graph.run(Graph.values(value="input"), commit=commits)

    settlements = root_settlements(commits)
    assert starts == ["a", "b"]
    assert tuple(transition.result.node_id for transition in settlements if transition.result is not None) == (
        "a",
        "b",
    )
    first_resources = settlements[0].candidate_state.resources
    assert first_resources is not None
    assert first_resources.acquisitions[0].node_id == GraphNodeId("b")
    assert first_resources.acquisitions[0].admitted


async def test_resource_free_and_resource_admitted_nodes_share_session_scheduler() -> None:
    starts: list[str] = []

    def operation(node_id: str) -> NodeCallable[str]:
        async def run(values: Graph.Values[str]) -> Graph.Values[str]:
            starts.append(node_id)
            return values

        return run

    graph = resource_graph(
        (
            ("a", operation("a"), ("file",)),
            ("b", operation("b"), ("file",)),
            ("x", operation("x"), ()),
        )
    )
    commits = CommitLog()
    result = await graph.run(Graph.values(value="input"), commit=commits, max_parallel_tasks=2)

    assert isinstance(result, Graph.CompletedResult)
    assert starts == ["a", "x", "b"]
    assert tuple(
        transition.result.node_id for transition in root_settlements(commits) if transition.result is not None
    ) == ("a", "x", "b")


async def test_ordinary_error_stops_unstarted_waiters_and_fence_clears_remaining_claim() -> None:
    waiter_calls = 0

    async def fail(_values: Graph.Values[str]) -> Graph.Values[str]:
        raise RuntimeError("owner failed")

    async def waiter(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal waiter_calls
        waiter_calls += 1
        return values

    graph = resource_graph(
        (
            ("a", fail, ("file",)),
            ("b", waiter, ("file",)),
        )
    )
    commits = CommitLog()

    with pytest.raises(RuntimeError, match="owner failed"):
        await graph.run(Graph.values(value="input"), commit=commits)

    assert waiter_calls == 0
    assert isinstance(commits.transitions[-1].command, FenceGraphExecution)
    assert commits.transitions[-1].candidate_state.execution is None
    assert commits.transitions[-1].candidate_state.resources is None


async def test_three_conflicting_resource_nodes_are_released_and_selected_fifo() -> None:
    starts: list[str] = []

    def operation(node_id: str) -> NodeCallable[str]:
        async def run(values: Graph.Values[str]) -> Graph.Values[str]:
            starts.append(node_id)
            return values

        return run

    graph = resource_graph(tuple((node_id, operation(node_id), ("file",)) for node_id in ("a", "b", "c")))
    await graph.run(Graph.values(value="input"))

    assert starts == ["a", "b", "c"]


async def test_partial_multi_resource_waiter_becomes_admitted_after_prefix_owner_settles() -> None:
    starts: list[str] = []

    def operation(node_id: str) -> NodeCallable[str]:
        async def run(values: Graph.Values[str]) -> Graph.Values[str]:
            starts.append(node_id)
            return values

        return run

    graph = resource_graph(
        (
            ("b", operation("b"), ("first", "second")),
            ("a", operation("a"), ("second",)),
        )
    )
    commits = CommitLog()
    await graph.run(Graph.values(value="input"), commit=commits, max_parallel_tasks=3)

    initial_resources = root_claims(commits)[0].resources
    assert initial_resources is not None
    b_initial = next(item for item in initial_resources.acquisitions if item.node_id == GraphNodeId("b"))
    assert b_initial.acquired == (ResourceId("first"),)
    assert b_initial.waiting_for == ResourceId("second")
    settlements = root_settlements(commits)
    after_a = settlements[0].candidate_state.resources
    assert after_a is not None
    b_after_a = next(item for item in after_a.acquisitions if item.node_id == GraphNodeId("b"))
    assert b_after_a.admitted
    assert starts == ["a", "b"]


async def test_resource_free_activation_has_no_fake_acquisition() -> None:
    graph = resource_graph((("free", echo, ()),))
    commits = CommitLog()
    result = await graph.run(Graph.values(value="input"), commit=commits)

    assert isinstance(result, Graph.CompletedResult)
    claim = root_claims(commits)[0]
    assert claim.resources is None


async def test_nonconflicting_resource_nodes_run_concurrently_in_the_same_scheduler() -> None:
    barrier = asyncio.Barrier(2)
    entered: list[str] = []

    def operation(node_id: str) -> NodeCallable[str]:
        async def run(values: Graph.Values[str]) -> Graph.Values[str]:
            entered.append(node_id)
            await asyncio.wait_for(barrier.wait(), timeout=1)
            return values

        return run

    graph = resource_graph(
        (
            ("a", operation("a"), ("first",)),
            ("b", operation("b"), ("second",)),
        )
    )
    result = await graph.run(Graph.values(value="input"), max_parallel_tasks=2)

    assert isinstance(result, Graph.CompletedResult)
    assert entered == ["a", "b"]


async def test_typed_resource_failure_releases_and_admits_its_waiter() -> None:
    calls: list[str] = []

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("a")
        return Graph.failure("typed failure")

    async def waiter(values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("b")
        return values

    graph = resource_graph(
        (
            ("a", fail, ("file",)),
            ("b", waiter, ("file",)),
        )
    )
    result = await graph.run(Graph.values(value="input"))

    assert isinstance(result, Graph.FailedResult)
    assert tuple(view.node_id for view in result.failures) == ("a",)
    assert calls == ["a", "b"]


async def test_conditional_frontier_admits_only_the_selected_resource_target() -> None:
    calls: list[str] = []

    async def route(values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(values, route="left")

    def branch(node_id: str) -> NodeCallable[str]:
        async def run(values: Graph.Values[str]) -> Graph.Values[str]:
            calls.append(node_id)
            return values

        return run

    graph = Graph[str]("resource.conditional")
    graph.add_node(
        "route",
        route,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    for node_id in ("left", "right"):
        graph.add_node(
            node_id,
            branch(node_id),
            inputs={"value": Graph.node_output("route", "value")},
            outputs={"value": str},
            resources=("file",),
        )
    graph.add_conditional_edge("route", "left", "left")
    graph.add_conditional_edge("route", "right", "right")
    graph.set_outputs({})
    commits = CommitLog()

    result = await graph.run(Graph.values(value="input"), commit=commits)

    assert isinstance(result, Graph.CompletedResult)
    resource_claims = tuple(command for command in root_claims(commits) if command.resources is not None)
    assert len(resource_claims) == 1
    resources = resource_claims[0].resources
    assert resources is not None
    assert tuple(item.node_id for item in resources.acquisitions) == (GraphNodeId("left"),)
    assert calls == ["left"]


async def test_missing_child_precedes_resource_admission() -> None:
    graph = nested_resource_graph()
    commits = CommitLog()
    result = await graph.run(Graph.values(value="input"), commit=commits)

    assert isinstance(result, Graph.CompletedResult)
    child_start = next(
        index
        for index, transition in enumerate(commits.transitions)
        if transition.scope == ("nested",) and transition.previous_state is None
    )
    root_claim = next(
        index
        for index, transition in enumerate(commits.transitions)
        if transition.scope == () and isinstance(transition.command, ClaimGraphExecution)
    )
    assert child_start < root_claim


@pytest.mark.parametrize("max_parallel_tasks", [1, 64])
async def test_active_child_does_not_block_resource_admission(max_parallel_tasks: int) -> None:
    child_entered = asyncio.Event()
    child_cleaned = asyncio.Event()
    resource_calls = 0

    async def blocking_child(values: Graph.Values[str]) -> Graph.Values[str]:
        child_entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            child_cleaned.set()
        return values

    async def resource(values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal resource_calls
        resource_calls += 1
        return values

    child = child_graph(blocking_child)
    graph = Graph[str]("resource.active-child")
    graph.add_node(
        "nested",
        child,
        inputs={"value": Graph.graph_input("value", str)},
    )
    graph.add_node(
        "resource",
        resource,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
        resources=("file",),
    )
    graph.set_outputs({})
    commits = CommitLog()
    running = asyncio.create_task(
        graph.run(
            Graph.values(value="input"),
            commit=commits,
            max_parallel_tasks=max_parallel_tasks,
        )
    )
    await child_entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert child_cleaned.is_set()
    assert resource_calls == 1
    assert any(
        transition.scope == () and isinstance(transition.command, ClaimGraphExecution)
        for transition in commits.transitions
    )
    assert tuple(
        transition.scope for transition in commits.transitions if isinstance(transition.command, AbortGraphRun)
    ) == (("nested",), ())


async def test_resource_sibling_settles_without_waiting_for_nested_completion() -> None:
    graph = nested_resource_graph()
    commits = CommitLog()
    result = await graph.run(Graph.values(value="input"), commit=commits)

    assert isinstance(result, Graph.CompletedResult)
    settlements = root_settlements(commits)
    assert tuple(transition.result.node_id for transition in settlements if transition.result is not None) == (
        "resource",
        "nested",
    )
    assert all(isinstance(transition.result, Graph.SuccessResult) for transition in settlements)


async def test_compiled_resource_requirement_drift_fails_before_scheduling() -> None:
    file_resource = ResourceId("file")
    database = ResourceId("database")
    original = internal_resource_graph(
        resource_order=(file_resource, database),
        requirement=(file_resource,),
    )
    _executor, claimed, _prepared = claimed_internal_state(original)
    drifted = internal_resource_graph(
        resource_order=(file_resource, database),
        requirement=(database,),
    )

    with pytest.raises(InvalidExecutionSnapshotError, match="exactly match"):
        require_snapshot_matches_graph(drifted, claimed)


@pytest.mark.parametrize("case", ["wrong-order", "requirement-drift", "stale-participant"])
async def test_committed_resource_snapshot_rejects_each_authority_mismatch(case: str) -> None:
    file_resource = ResourceId("file")
    database = ResourceId("database")
    original = internal_resource_graph(
        resource_order=(file_resource, database),
        requirement=(file_resource,),
    )
    _executor, claimed, _prepared = claimed_internal_state(original)
    if case == "stale-participant":
        stale = replace(
            claimed,
            resources=ResourceSnapshot(
                (
                    ResourceLock(file_resource, GraphNodeId("stale")),
                    ResourceLock(database),
                ),
                (
                    ResourceAcquisition(
                        GraphNodeId("stale"),
                        (file_resource,),
                        (file_resource,),
                    ),
                ),
            ),
        )
        with pytest.raises(InvalidExecutionSnapshotError):
            require_snapshot_matches_graph(original, stale)
        return

    candidate = (
        internal_resource_graph(
            resource_order=(database, file_resource),
            requirement=(file_resource,),
        )
        if case == "wrong-order"
        else internal_resource_graph(
            resource_order=(file_resource, database),
            requirement=(database,),
        )
    )

    with pytest.raises(InvalidExecutionSnapshotError):
        require_snapshot_matches_graph(candidate, claimed)


async def test_competing_resource_claims_have_one_durable_winner() -> None:
    file_resource = ResourceId("file")
    graph = internal_resource_graph(
        resource_order=(file_resource,),
        requirement=(file_resource,),
    )
    executor = GraphExecutor(graph)
    state = reduce_graph_run(None, project_start_graph_command(graph, GraphRunId("run")))
    execution_request = step_request(graph, state, "input").execution_request()
    first, second = (
        executor.prepare(execution_request),
        executor.prepare(execution_request),
    )
    assert isinstance(first, ExecutableFrontier)
    assert isinstance(second, ExecutableFrontier)
    winner = reduce_graph_run(state, first.claim.command)

    with pytest.raises(GraphStateTransitionError, match="stale revision"):
        reduce_graph_run(winner, second.claim.command)


async def test_claimed_resource_session_revalidates_exact_participants() -> None:
    file_resource = ResourceId("file")
    graph = internal_resource_graph(
        resource_order=(file_resource,),
        requirement=(file_resource,),
    )
    executor, claimed, prepared = claimed_internal_state(graph)
    forged = replace(claimed, resources=None)
    with pytest.raises(InvalidExecutionSnapshotError, match="compiled resource"):
        executor.issue_session(prepared.claim, forged)
    session = executor.issue_session(prepared.claim, claimed)
    await session.aclose()


async def test_later_waiter_error_preserves_earlier_authoritative_settlement() -> None:
    async def later_error(_values: Graph.Values[str]) -> Graph.Values[str]:
        raise RuntimeError("waiter failed")

    graph = resource_graph(
        (
            ("a", echo, ("file",)),
            ("b", later_error, ("file",)),
        )
    )
    commits = CommitLog()

    with pytest.raises(RuntimeError, match="waiter failed"):
        await graph.run(Graph.values(value="input"), commit=commits)

    fenced = commits.transitions[-1].candidate_state
    assert isinstance(fenced.frontier.nodes[0].settlement, SucceededGraphNode)
    assert isinstance(fenced.frontier.nodes[1].settlement, PendingGraphNode)


async def test_resource_waiters_preserve_mixed_failure_and_interrupt_outcomes() -> None:
    calls: list[str] = []

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("a")
        return Graph.failure("failed")

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        calls.append("b")
        return Graph.interrupt(b"question")

    async def succeed(values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append("c")
        return values

    graph = resource_graph(
        (
            ("a", fail, ("file",)),
            ("b", interrupt, ("file",)),
            ("c", succeed, ("file",)),
        ),
        codec=Codec(),
    )
    result = await graph.run(Graph.values(value="input"))

    assert isinstance(result, Graph.FailedResult)
    assert tuple(view.node_id for view in result.failures) == ("a",)
    assert tuple(view.node_id for view in result.interrupts) == ("b",)
    assert calls == ["a", "b", "c"]


async def test_interrupt_inputs_survive_resource_admission_per_node() -> None:
    received: dict[str, list[str]] = {"a": [], "b": []}

    def operation(node_id: str) -> NodeCallable[str]:
        async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
            received[node_id].append(values["value"])
            if len(received[node_id]) == 1:
                return Graph.interrupt(f"question-{node_id}".encode())
            return values

        return interrupt_once

    graph = resource_graph(
        (
            ("a", operation("a"), ("file",)),
            ("b", operation("b"), ("file",)),
        ),
        codec=Codec(),
    )
    first = await graph.run(Graph.values(value="initial"))
    assert isinstance(first, Graph.AwaitingResumeResult)
    by_node = {interrupt.node_id: interrupt for interrupt in first.interrupts}
    completed = await graph.run(
        state=first.state,
        continuation=first.continuation,
        resume=(
            graph.resume_interrupted(
                "a",
                by_node["a"].interrupt_id,
                Graph.values(value="override-a"),
            ),
            graph.resume_interrupted(
                "b",
                by_node["b"].interrupt_id,
                Graph.values(value="override-b"),
            ),
        ),
    )

    assert isinstance(completed, Graph.CompletedResult)
    assert received == {
        "a": ["initial", "override-a"],
        "b": ["initial", "override-b"],
    }

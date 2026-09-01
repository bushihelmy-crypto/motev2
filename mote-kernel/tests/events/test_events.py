"""Deterministic tests for atomic graph settlement event decoration."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass, field, fields, replace
from typing import cast

import pytest

import mote_kernel.events as events_package
import mote_kernel.events.commit as commit_module
from mote_kernel.events import EventingGraphCommit
from mote_kernel.events.commit import AtomicCommitRequest, AtomicPersistenceCommit
from mote_kernel.events.identity import NODE_SETTLEMENT_EVENT_SCHEMA_VERSION, node_settlement_event_id
from mote_kernel.events.record import NodeSettlementEventReference
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.node import NodeCallable
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId, SettleGraphNode, child_graph_run_id


def _empty_requests() -> list[AtomicCommitRequest[str]]:
    return []


@dataclass
class RecordingPersistence:
    """The atomic persistence Port test double; one call is one transaction."""

    requests: list[AtomicCommitRequest[str]] = field(default_factory=_empty_requests)

    async def __call__(self, request: AtomicCommitRequest[str], /) -> Graph.State:
        self.requests.append(request)
        return request.transition.candidate_state


def _encode_resume(values: Graph.Values[str]) -> bytes:
    return values["value"].encode()


def _decode_resume(payload: bytes) -> Graph.Values[str]:
    return Graph.values(value=payload.decode())


def _graph(
    operation: NodeCallable[str],
    *,
    definition_id: str,
    resume_codec: bool = False,
) -> Graph[str]:
    graph = Graph[str](definition_id)
    if resume_codec:
        graph.set_resume_codec("events-resume", 1, _encode_resume, _decode_resume)
    graph.add_node(
        "work",
        operation,
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    graph.add_edge("work", Graph.END)
    graph.set_outputs({"value": Graph.node_output("work", "value")})
    return graph


async def _identity(values: Graph.Values[str]) -> Graph.Values[str]:
    return values


async def _failure(_values: Graph.Values[str]) -> Graph.Outcome[str]:
    return Graph.failure("declined")


async def _interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
    return Graph.interrupt(b"approval")


def _decorated_commit(
    persistence: AtomicPersistenceCommit[str],
) -> Graph.Commit[str]:
    return EventingGraphCommit(persistence)


async def _run(
    operation: NodeCallable[str],
    persistence: RecordingPersistence,
    *,
    definition_id: str,
    resume_codec: bool = False,
) -> Graph.Result[str]:
    graph = _graph(operation, definition_id=definition_id, resume_codec=resume_codec)
    return await graph.run(
        Graph.values(value="input"),
        run_id="run-1",
        commit=_decorated_commit(persistence),
    )


def _settlement_requests(
    persistence: RecordingPersistence,
) -> tuple[AtomicCommitRequest[str], ...]:
    return tuple(request for request in persistence.requests if isinstance(request.transition.command, SettleGraphNode))


def _settlement_command(request: AtomicCommitRequest[str]) -> SettleGraphNode:
    command = request.transition.command
    if not isinstance(command, SettleGraphNode):
        raise AssertionError("expected a node settlement request")
    return command


def _settlement_event(request: AtomicCommitRequest[str]) -> NodeSettlementEventReference:
    event = request.event_reference
    if event is None:
        raise AssertionError("expected a node settlement event reference")
    return event


def _assert_reference_matches_transition(request: AtomicCommitRequest[str]) -> None:
    transition = request.transition
    command = _settlement_command(request)
    event = _settlement_event(request)
    assert event.run_id == transition.candidate_state.run_id
    assert event.scope == transition.scope
    assert event.superstep == transition.candidate_state.superstep
    assert event.node_id == command.outcome.node_id
    assert event.execution_generation == command.execution.generation
    assert event.settlement_revision == transition.candidate_state.revision


async def _captured_settlement_transition() -> Graph.Transition[str]:
    persistence = RecordingPersistence()
    result = await _run(_identity, persistence, definition_id="events.capture")
    assert isinstance(result, Graph.CompletedResult)
    return _settlement_requests(persistence)[0].transition


def test_event_package_exposes_only_the_commit_decorator() -> None:
    assert events_package.__all__ == ["EventingGraphCommit"]
    assert events_package.EventingGraphCommit is EventingGraphCommit
    assert not hasattr(events_package, "AtomicCommitRequest")
    assert not hasattr(events_package, "AtomicPersistenceCommit")
    assert not hasattr(events_package, "EventSink")
    assert not hasattr(events_package, "NodeSettlementEventReference")


def test_internal_records_are_exact_immutable_transaction_values() -> None:
    reference = NodeSettlementEventReference(
        run_id=GraphRunId("run"),
        scope=(),
        superstep=0,
        node_id=GraphNodeId("node"),
        execution_generation=1,
        settlement_revision=3,
    )
    assert tuple(item.name for item in fields(NodeSettlementEventReference)) == (
        "run_id",
        "scope",
        "superstep",
        "node_id",
        "execution_generation",
        "settlement_revision",
    )
    assert not hasattr(reference, "input")
    assert not hasattr(reference, "output")
    assert not hasattr(reference, "outcome")
    assert not hasattr(reference, "result")
    assert reference.schema_version == NODE_SETTLEMENT_EVENT_SCHEMA_VERSION
    assert NodeSettlementEventReference.schema_version == NODE_SETTLEMENT_EVENT_SCHEMA_VERSION

    with pytest.raises(FrozenInstanceError):
        reference.node_id = GraphNodeId("other")  # type: ignore[misc]


def test_internal_records_reject_noncanonical_or_mutable_coordinates() -> None:
    reference = NodeSettlementEventReference(
        run_id=GraphRunId("run"),
        scope=("child",),
        superstep=0,
        node_id=GraphNodeId("node"),
        execution_generation=1,
        settlement_revision=0,
    )

    with pytest.raises(ValueError, match="identities"):
        replace(reference, run_id=GraphRunId(""))
    with pytest.raises(ValueError, match="identities"):
        replace(reference, node_id=GraphNodeId(" node"))
    with pytest.raises(ValueError, match="scope"):
        replace(reference, scope=cast(tuple[str, ...], ["child"]))
    with pytest.raises(ValueError, match="scope"):
        replace(reference, scope=("",))
    with pytest.raises(ValueError, match="superstep"):
        replace(reference, superstep=-1)
    with pytest.raises(ValueError, match="superstep"):
        replace(reference, superstep=cast(int, True))
    with pytest.raises(ValueError, match="execution generation"):
        replace(reference, execution_generation=0)
    with pytest.raises(ValueError, match="execution generation"):
        replace(reference, execution_generation=cast(int, True))
    with pytest.raises(ValueError, match="settlement revision"):
        replace(reference, settlement_revision=-1)
    with pytest.raises(ValueError, match="settlement revision"):
        replace(reference, settlement_revision=cast(int, True))


def test_event_identity_is_deterministic_and_coordinate_complete() -> None:
    reference = NodeSettlementEventReference(
        run_id=GraphRunId("run"),
        scope=("child",),
        superstep=2,
        node_id=GraphNodeId("node"),
        execution_generation=4,
        settlement_revision=9,
    )
    assert reference.event_id == node_settlement_event_id(
        reference.run_id,
        reference.scope,
        reference.superstep,
        reference.node_id,
        reference.execution_generation,
        reference.settlement_revision,
    )
    assert reference.event_id == replace(reference).event_id
    assert reference.event_id != replace(reference, settlement_revision=10).event_id
    assert reference.event_id != replace(reference, execution_generation=5).event_id
    assert replace(reference, scope=("a", "bc")).event_id != replace(reference, scope=("ab", "c")).event_id


@pytest.mark.asyncio
async def test_projection_failure_prevents_any_persistence_call(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def reject(_transition: Graph.Transition[str]) -> NodeSettlementEventReference | None:
        raise ValueError("invalid event coordinate")

    async def persistence(_request: AtomicCommitRequest[str], /) -> Graph.State:
        nonlocal called
        called = True
        raise AssertionError("persistence must not run after projection failure")

    monkeypatch.setattr(commit_module, "project_event", reject)
    commit = EventingGraphCommit[str](persistence)
    with pytest.raises(ValueError, match="invalid event coordinate"):
        await commit(cast(Graph.Transition[str], object()))
    assert not called


@pytest.mark.asyncio
async def test_every_successful_node_settlement_enters_the_atomic_request_once() -> None:
    persistence = RecordingPersistence()

    result = await _run(_identity, persistence, definition_id="events.success")

    assert isinstance(result, Graph.CompletedResult)
    settlements = _settlement_requests(persistence)
    assert len(settlements) == 1
    _assert_reference_matches_transition(settlements[0])
    assert len(persistence.requests) > len(settlements)
    assert all(
        request.event_reference is None
        for request in persistence.requests
        if not isinstance(request.transition.command, SettleGraphNode)
    )


@pytest.mark.asyncio
async def test_each_conditional_node_still_has_exactly_one_settlement_reference() -> None:
    async def choose(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.success(Graph.values(), route="approved")

    async def target(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("events.conditional")
    graph.add_node("choose", choose, inputs={}, outputs={})
    graph.add_node("target", target, inputs={}, outputs={})
    graph.add_conditional_edge("choose", "approved", "target")
    graph.set_outputs({})
    persistence = RecordingPersistence()

    result = await graph.run(
        Graph.values(),
        run_id="conditional-run",
        commit=_decorated_commit(persistence),
    )

    assert isinstance(result, Graph.CompletedResult)
    settlements = _settlement_requests(persistence)
    assert tuple(_settlement_event(request).node_id for request in settlements) == (
        GraphNodeId("choose"),
        GraphNodeId("target"),
    )
    for request in settlements:
        _assert_reference_matches_transition(request)


@pytest.mark.asyncio
async def test_failure_and_interrupt_settlements_use_the_same_reference_contract() -> None:
    failure_persistence = RecordingPersistence()
    failure = await _run(
        _failure,
        failure_persistence,
        definition_id="events.failure",
    )
    assert not isinstance(failure, Graph.CompletedResult)
    failure_request = _settlement_requests(failure_persistence)[0]
    _assert_reference_matches_transition(failure_request)

    interrupt_persistence = RecordingPersistence()
    interrupted = await _run(
        _interrupt,
        interrupt_persistence,
        definition_id="events.interrupt",
        resume_codec=True,
    )
    assert isinstance(interrupted, Graph.AwaitingResumeResult)
    interrupt_request = _settlement_requests(interrupt_persistence)[0]
    _assert_reference_matches_transition(interrupt_request)


@pytest.mark.asyncio
async def test_unhandled_node_exception_creates_no_settlement_or_event_reference() -> None:
    async def explode(_values: Graph.Values[str]) -> Graph.Values[str]:
        raise RuntimeError("provider exploded")

    persistence = RecordingPersistence()
    with pytest.raises(RuntimeError, match="provider exploded"):
        await _run(explode, persistence, definition_id="events.unhandled-error")

    assert _settlement_requests(persistence) == ()
    assert all(request.event_reference is None for request in persistence.requests)


@pytest.mark.asyncio
async def test_resume_command_adds_no_event_beyond_the_new_node_settlement() -> None:
    attempts = 0

    async def interrupt_once(values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return Graph.interrupt(b"question")
        return values

    graph = _graph(interrupt_once, definition_id="events.interrupt-recovery", resume_codec=True)
    persistence = RecordingPersistence()
    commit = _decorated_commit(persistence)

    paused = await graph.run(Graph.values(value="input"), run_id="resume-run", commit=commit)
    assert isinstance(paused, Graph.AwaitingResumeResult)
    first_event_id = _settlement_event(_settlement_requests(persistence)[0]).event_id

    resumed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            graph.resume_interrupted(
                "work",
                paused.interrupts[0].interrupt_id,
                Graph.values(value="answer"),
            ),
        ),
        commit=commit,
    )

    assert isinstance(resumed, Graph.CompletedResult)
    settlements = _settlement_requests(persistence)
    assert len(settlements) == 2
    first = _settlement_event(settlements[0])
    second = _settlement_event(settlements[1])
    assert first.event_id == first_event_id
    assert first.execution_generation < second.execution_generation
    assert first.settlement_revision < second.settlement_revision
    assert second.event_id != first_event_id
    assert all(
        request.event_reference is None
        for request in persistence.requests
        if not isinstance(request.transition.command, SettleGraphNode)
    )


@pytest.mark.asyncio
async def test_reprojecting_the_same_transition_reuses_the_same_event_id() -> None:
    transition = await _captured_settlement_transition()
    persistence = RecordingPersistence()
    commit = _decorated_commit(persistence)

    assert await commit(transition) == transition.candidate_state
    assert await commit(transition) == transition.candidate_state

    first, second = persistence.requests
    assert first.transition is second.transition is transition
    assert _settlement_event(first).event_id == _settlement_event(second).event_id
    with pytest.raises(FrozenInstanceError):
        first.event_reference = None  # type: ignore[misc]


@pytest.mark.asyncio
async def test_parallel_settlements_keep_reference_and_transition_paired_without_global_order() -> None:
    release_a = asyncio.Event()

    async def a(_values: Graph.Values[str]) -> Graph.Values[str]:
        await release_a.wait()
        return Graph.values()

    async def b(_values: Graph.Values[str]) -> Graph.Values[str]:
        release_a.set()
        return Graph.values()

    async def c(_values: Graph.Values[str]) -> Graph.Values[str]:
        await asyncio.sleep(0)
        return Graph.values()

    graph = Graph[str]("events.parallel")
    for node_id, operation in (("c", c), ("b", b), ("a", a)):
        graph.add_node(node_id, operation, inputs={}, outputs={})
    graph.set_outputs({})
    persistence = RecordingPersistence()

    result = await graph.run(
        Graph.values(),
        run_id="parallel-run",
        commit=_decorated_commit(persistence),
        max_parallel_tasks=3,
    )

    assert isinstance(result, Graph.CompletedResult)
    settlements = _settlement_requests(persistence)
    assert {_settlement_event(request).node_id for request in settlements} == {
        GraphNodeId("a"),
        GraphNodeId("b"),
        GraphNodeId("c"),
    }
    for request in settlements:
        _assert_reference_matches_transition(request)


@pytest.mark.asyncio
async def test_nested_settlements_keep_child_scope_and_run_identity() -> None:
    async def child_operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    def child(definition_id: str) -> Graph[str]:
        graph = Graph[str](definition_id)
        graph.add_node("leaf", child_operation, inputs={}, outputs={})
        graph.set_outputs({})
        return graph

    parent = Graph[str]("events.nested.parent")
    parent.add_node("right", child("events.nested.right"), inputs={})
    parent.add_node("left", child("events.nested.left"), inputs={})
    parent.set_outputs({})
    persistence = RecordingPersistence()

    result = await parent.run(
        Graph.values(),
        run_id="nested-root",
        commit=_decorated_commit(persistence),
        max_parallel_tasks=2,
    )

    assert isinstance(result, Graph.CompletedResult)
    settlements = _settlement_requests(persistence)
    assert len(settlements) == 4
    by_coordinate = {
        (event.scope, event.node_id): event for event in (_settlement_event(request) for request in settlements)
    }
    assert set(by_coordinate) == {
        (("left",), GraphNodeId("leaf")),
        (("right",), GraphNodeId("leaf")),
        ((), GraphNodeId("left")),
        ((), GraphNodeId("right")),
    }
    assert by_coordinate[(("left",), GraphNodeId("leaf"))].run_id == child_graph_run_id(
        GraphRunId("nested-root"),
        0,
        GraphNodeId("left"),
    )
    assert by_coordinate[(("right",), GraphNodeId("leaf"))].run_id == child_graph_run_id(
        GraphRunId("nested-root"),
        0,
        GraphNodeId("right"),
    )
    for request in settlements:
        _assert_reference_matches_transition(request)


@pytest.mark.asyncio
async def test_persistence_failure_rejects_the_whole_settlement_request() -> None:
    class SettlementCommitError(RuntimeError):
        pass

    class FailingPersistence(RecordingPersistence):
        def __init__(self) -> None:
            super().__init__()
            self.error = SettlementCommitError("settlement rejected")
            self.committed: list[AtomicCommitRequest[str]] = []

        async def __call__(self, request: AtomicCommitRequest[str], /) -> Graph.State:
            self.requests.append(request)
            command = request.transition.command
            if isinstance(command, SettleGraphNode) and command.outcome.node_id == GraphNodeId("b"):
                raise self.error
            self.committed.append(request)
            return request.transition.candidate_state

    async def operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("events.persistence-failure")
    for node_id in ("a", "b", "c"):
        graph.add_node(node_id, operation, inputs={}, outputs={})
    graph.set_outputs({})
    persistence = FailingPersistence()

    with pytest.raises(SettlementCommitError) as raised:
        await graph.run(
            Graph.values(),
            run_id="failure-run",
            commit=_decorated_commit(persistence),
            max_parallel_tasks=1,
        )

    assert raised.value is persistence.error
    attempted = _settlement_requests(persistence)
    committed = tuple(
        request for request in persistence.committed if isinstance(request.transition.command, SettleGraphNode)
    )
    assert tuple(_settlement_event(request).node_id for request in attempted) == (
        GraphNodeId("a"),
        GraphNodeId("b"),
    )
    assert tuple(_settlement_event(request).node_id for request in committed) == (GraphNodeId("a"),)


@pytest.mark.asyncio
async def test_unacknowledged_atomic_request_is_rejected_by_graph_owner() -> None:
    async def operation(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    class LostAcknowledgementPersistence(RecordingPersistence):
        async def __call__(self, request: AtomicCommitRequest[str], /) -> Graph.State:
            confirmed = await super().__call__(request)
            if request.event_reference is not None:
                return replace(confirmed, revision=confirmed.revision + 1)
            return confirmed

    graph = Graph[str]("events.unacknowledged-settlement")
    graph.add_node("node", operation, inputs={}, outputs={})
    graph.set_outputs({})
    persistence = LostAcknowledgementPersistence()

    with pytest.raises(Graph.SnapshotMismatchError, match="exact authoritative"):
        await graph.run(
            Graph.values(),
            run_id="unacknowledged-run",
            commit=_decorated_commit(persistence),
        )

    settlements = _settlement_requests(persistence)
    assert len(settlements) == 1
    _assert_reference_matches_transition(settlements[0])


@pytest.mark.asyncio
async def test_graph_waits_for_the_atomic_local_transaction() -> None:
    class SlowPersistence(RecordingPersistence):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(self, request: AtomicCommitRequest[str], /) -> Graph.State:
            if request.event_reference is not None:
                self.entered.set()
                await self.release.wait()
            return await super().__call__(request)

    persistence = SlowPersistence()
    graph = _graph(_identity, definition_id="events.slow-persistence")
    running = asyncio.create_task(
        graph.run(
            Graph.values(value="input"),
            run_id="slow-run",
            commit=_decorated_commit(persistence),
        )
    )

    await asyncio.wait_for(persistence.entered.wait(), timeout=1)
    await asyncio.sleep(0)
    assert not running.done()
    persistence.release.set()

    result = await asyncio.wait_for(running, timeout=1)
    assert isinstance(result, Graph.CompletedResult)
    assert len(_settlement_requests(persistence)) == 1


@pytest.mark.asyncio
async def test_persistence_exception_and_cancellation_propagate_unchanged() -> None:
    transition = await _captured_settlement_transition()

    class RaisingPersistence:
        def __init__(self, error: RuntimeError | asyncio.CancelledError) -> None:
            self.error = error
            self.requests: list[AtomicCommitRequest[str]] = []

        async def __call__(self, request: AtomicCommitRequest[str], /) -> Graph.State:
            self.requests.append(request)
            raise self.error

    failure = RaisingPersistence(RuntimeError("transaction failed"))
    with pytest.raises(RuntimeError, match="transaction failed") as failed:
        await EventingGraphCommit[str](failure)(transition)
    assert failed.value is failure.error
    assert len(failure.requests) == 1
    _assert_reference_matches_transition(failure.requests[0])

    cancellation = RaisingPersistence(asyncio.CancelledError("transaction cancelled"))
    with pytest.raises(asyncio.CancelledError, match="transaction cancelled") as cancelled:
        await EventingGraphCommit[str](cancellation)(transition)
    assert cancelled.value is cancellation.error
    assert len(cancellation.requests) == 1
    _assert_reference_matches_transition(cancellation.requests[0])


@pytest.mark.asyncio
async def test_event_commit_composes_inside_an_outer_commit_decorator() -> None:
    trace: list[str] = []

    class TracedPersistence(RecordingPersistence):
        async def __call__(self, request: AtomicCommitRequest[str], /) -> Graph.State:
            trace.append("persistence")
            return await super().__call__(request)

    @dataclass(frozen=True, slots=True)
    class OuterCommit:
        inner: Graph.Commit[str]

        async def __call__(self, transition: Graph.Transition[str], /) -> Graph.State:
            trace.append("outer-before")
            confirmed = await self.inner(transition)
            trace.append("outer-after")
            return confirmed

    graph = _graph(_identity, definition_id="events.decorator-chain")
    persistence = TracedPersistence()
    commit = OuterCommit(_decorated_commit(persistence))

    result = await graph.run(Graph.values(value="input"), run_id="chain-run", commit=commit)

    assert isinstance(result, Graph.CompletedResult)
    assert len(trace) == 3 * len(persistence.requests)
    assert all(
        trace[index : index + 3] == ["outer-before", "persistence", "outer-after"] for index in range(0, len(trace), 3)
    )
    assert len(_settlement_requests(persistence)) == 1


@pytest.mark.asyncio
async def test_one_eventing_decorator_is_safe_for_concurrent_runs() -> None:
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        await asyncio.sleep(0.01 if values["value"] == "first" else 0)
        return values

    graph = _graph(operation, definition_id="events.concurrent-runs")
    persistence = RecordingPersistence()
    commit = _decorated_commit(persistence)
    results = await asyncio.gather(
        graph.run(Graph.values(value="first"), run_id="run-a", commit=commit),
        graph.run(Graph.values(value="second"), run_id="run-b", commit=commit),
    )

    assert all(isinstance(result, Graph.CompletedResult) for result in results)
    settlements = _settlement_requests(persistence)
    assert len(settlements) == 2
    assert {_settlement_event(request).run_id for request in settlements} == {
        GraphRunId("run-a"),
        GraphRunId("run-b"),
    }
    for request in settlements:
        _assert_reference_matches_transition(request)

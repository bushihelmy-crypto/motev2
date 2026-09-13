import asyncio
from collections import Counter

import pytest
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence, nested_graph

from mote_kernel.execution import Graph
from mote_kernel.execution.identity import child_scope_run, root_scope_run
from mote_kernel.execution.persistence import DurableGraphCommit, GraphPersistenceCommit, GraphRecovery
from mote_kernel.state.graph_state import GraphNodeId, GraphRunId, GraphRunStatus


def at_phase(request: GraphPersistenceCommit[str], phase: str, depth: int) -> bool:
    if request.scope != (GraphNodeId("child"),) * depth:
        return False
    if phase == "start":
        return request.expected_revision is None
    if phase == "claim":
        return request.candidate_state.execution is not None and not request.writes.publications
    if phase == "first" or phase == "second":
        return any(publication.coordinate.activation.node_id == phase for publication in request.writes.publications)
    if phase == "advance":
        return request.candidate_state.superstep == 1 and request.candidate_state.execution_sequence == 1
    return request.candidate_state.status is GraphRunStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "claim", "first", "advance", "second", "complete"])
@pytest.mark.parametrize("boundary", ["before-write", "after-write"])
@pytest.mark.parametrize("cancelled", [False, True], ids=["io-error", "commit-cancelled"])
@pytest.mark.parametrize("depth", [0, 1], ids=["root", "child"])
async def test_every_commit_boundary_recovers_only_authoritative_facts(
    phase: str, boundary: str, cancelled: bool, depth: int
) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()

    async def fail(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        if at_phase(request, phase, depth):
            if boundary == "after-write":
                await store(request)
            store.unavailable = True
            if cancelled:
                raise asyncio.CancelledError("commit confirmation unavailable")
            raise OSError("commit confirmation unavailable")
        return await store(request)

    with pytest.raises(asyncio.CancelledError if cancelled else OSError, match="commit confirmation unavailable"):
        await nested_graph(calls, depth=depth).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, fail)
        )
    initial = [] if phase in ("start", "claim") else ["first"] if phase in ("first", "advance") else ["first", "second"]
    assert calls == initial
    before_recovery = tuple(store.requests)
    store.reopen()
    root = root_scope_run(GraphRunId("run"))
    if root not in store.states:
        assert phase == "start" and boundary == "before-write" and depth == 0
        completed = await nested_graph(calls, depth=depth).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    else:
        child_reads = (child_scope_run(root, 0, GraphNodeId("child")),) if depth else ()
        completed = await nested_graph(calls, depth=depth).run(
            recovery=GraphRecovery(store.checkpoint(child_reads=child_reads), DurableGraphCommit(STRING_CODEC, store))
        )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "input-first-second"
    assert completed.state == store.states[root]
    expected = Counter(("first", "second"))
    if phase in ("first", "second") and boundary == "before-write":
        expected[phase] += 1
    assert Counter(calls) == expected
    assert tuple(store.requests[: len(before_recovery)]) == before_recovery
    replayed = await nested_graph(calls, depth=depth).run(
        recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
    )
    assert isinstance(replayed, Graph.CompletedResult)
    assert replayed.outputs["value"] == completed.outputs["value"]
    assert Counter(calls) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "first"])
@pytest.mark.parametrize("depth", [0, 1], ids=["root", "child"])
async def test_waiter_cancellation_does_not_cancel_an_inflight_atomic_commit(phase: str, depth: int) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    entered = asyncio.Event()
    release = asyncio.Event()
    held: list[GraphPersistenceCommit[str]] = []

    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        if at_phase(request, phase, depth) and not held:
            held.append(request)
            entered.set()
            await release.wait()
        return await store(request)

    task = asyncio.create_task(
        nested_graph(calls, depth=depth).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, writer)
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert held[0] in store.requests
    assert calls == ([] if phase == "start" else ["first"])
    root = root_scope_run(GraphRunId("run"))
    child_reads = (child_scope_run(root, 0, GraphNodeId("child")),) if depth else ()
    checkpoint = store.checkpoint(child_reads=child_reads)
    confirmed = tuple(store.requests)
    recovered = await nested_graph(calls, depth=depth).run(
        recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store))
    )
    assert isinstance(recovered, Graph.AbortedResult)
    assert recovered.state == store.states[root]
    assert calls == ([] if phase == "start" else ["first"])
    assert tuple(store.requests) == confirmed

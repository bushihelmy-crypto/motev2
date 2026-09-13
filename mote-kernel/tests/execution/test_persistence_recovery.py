from dataclasses import replace

import pytest
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence, linear_graph, nested_graph

from mote_kernel.execution import Graph
from mote_kernel.execution.identity import root_scope_run
from mote_kernel.execution.persistence import DurableGraphCommit, GraphRecovery


@pytest.mark.asyncio
async def test_cold_recovery_uses_confirmed_values_in_a_new_graph() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: request.candidate_state.superstep == 1
    with pytest.raises(OSError, match="injected persistence outage"):
        await linear_graph(calls).run(
            Graph.values(value="初始\n输入"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    assert calls == ["first"]
    checkpoint = store.checkpoint()
    assert checkpoint.publications[0].frame.payload == '{"value":"初始\\n输入-first"}'.encode()
    store.reopen()
    result = await linear_graph(calls).run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))
    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "初始\n输入-first-second"
    assert calls == ["first", "second"]
    assert result.state == store.states[root_scope_run(result.state.run_id)]
    replayed = await linear_graph(calls).run(
        recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
    )
    assert isinstance(replayed, Graph.CompletedResult)
    assert replayed.state == result.state
    assert replayed.outputs["value"] == result.outputs["value"]
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_completed_checkpoint_replays_without_running_nodes_or_committing() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    await linear_graph(calls).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    committed = tuple(store.requests)
    result = await linear_graph(calls).run(
        recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
    )
    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "input-first-second"
    assert calls == ["first", "second"]
    assert tuple(store.requests) == committed


@pytest.mark.asyncio
async def test_completed_child_is_rebuilt_before_parent_settlement() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()

    store.fail_when = lambda request: not request.scope and bool(request.writes.publications)
    with pytest.raises(OSError, match="injected persistence outage"):
        await nested_graph(calls).run(
            Graph.values(value="nested"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    checkpoint = store.checkpoint()
    assert len(checkpoint.child_runs) == 1
    store.reopen()
    result = await nested_graph(calls).run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))
    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "nested-first-second"
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["input", "publication"])
async def test_missing_durable_evidence_fails_before_any_commit(missing: str) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    await linear_graph(calls).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    checkpoint = (
        replace(checkpoint, graph_inputs=())
        if missing == "input"
        else replace(checkpoint, publications=checkpoint.publications[:-1])
    )
    store.unavailable = True
    with pytest.raises(Graph.SnapshotMismatchError, match=r"graph input|graph output"):
        await linear_graph(calls).run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))
    assert calls == ["first", "second"]

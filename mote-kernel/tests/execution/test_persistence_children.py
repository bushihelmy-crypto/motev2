from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence, nested_graph

from mote_kernel.execution import Graph
from mote_kernel.execution.commit import GraphCommitKey
from mote_kernel.execution.engine.claim_stage import project_claim_command
from mote_kernel.execution.identity import ScopeRunCoordinate, child_scope_run, root_scope_run
from mote_kernel.execution.persistence import (
    DurableGraphCommit,
    GraphPersistenceCommit,
    GraphPersistenceWriteSet,
    GraphRecovery,
)
from mote_kernel.execution.run_context import ScopedStateBinding, UncreatedGraphRun
from mote_kernel.state.graph_state import GraphExecutionAttemptId, GraphNodeId, GraphRunId, reduce_graph_run


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [1, 3])
async def test_uncreated_child_is_started_only_after_explicit_authoritative_negative_read(depth: int) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: bool(request.scope) and request.expected_revision is None
    with pytest.raises(OSError):
        await nested_graph(calls, depth=depth).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    checkpoint = store.checkpoint()
    assert checkpoint.child_runs == ()
    assert calls == []
    coordinate = child_scope_run(root_scope_run(checkpoint.root_state.run_id), 0, GraphNodeId("child"))
    checkpoint = store.checkpoint(child_reads=(coordinate,))
    assert checkpoint.child_runs == (UncreatedGraphRun(coordinate),)
    store.reopen()
    completed = await nested_graph(calls, depth=depth).run(
        recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store))
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["value"] == "input-first-second"
    assert completed.state == store.states[root_scope_run(completed.state.run_id)]
    created = store.checkpoint().child_runs
    assert len(created) == depth
    assert all(isinstance(binding, ScopedStateBinding) for binding in created)
    replayed = await nested_graph(calls, depth=depth).run(
        recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
    )
    assert isinstance(replayed, Graph.CompletedResult)
    assert replayed.outputs["value"] == completed.outputs["value"]
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["missing", "foreign-run", "foreign-scope", "duplicate", "descendant-without-parent"])
async def test_uncreated_child_requires_exact_unique_parent_owned_proof(proof: str) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: bool(request.scope)
    with pytest.raises(OSError):
        await nested_graph(calls).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    checkpoint = store.checkpoint()
    coordinate = child_scope_run(root_scope_run(checkpoint.root_state.run_id), 0, GraphNodeId("child"))
    if proof == "missing":
        children = ()
    elif proof == "foreign-run":
        children = (UncreatedGraphRun(replace(coordinate, graph_run_id=GraphRunId("foreign"))),)
    elif proof == "foreign-scope":
        children = (UncreatedGraphRun(replace(coordinate, scope=(GraphNodeId("unknown"),))),)
    elif proof == "duplicate":
        children = (UncreatedGraphRun(coordinate), UncreatedGraphRun(coordinate))
    else:
        children = (UncreatedGraphRun(child_scope_run(coordinate, 0, GraphNodeId("child"))),)
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError):
        await nested_graph(calls).run(
            recovery=GraphRecovery(replace(checkpoint, child_runs=children), DurableGraphCommit(STRING_CODEC, store))
        )
    assert len(store.requests) == committed
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["omitted", "uncreated", "conflicting"])
async def test_completed_parent_requires_its_created_child_snapshot(proof: str) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    await nested_graph(calls).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    child = checkpoint.child_runs[0]
    if proof == "conflicting":
        changed = deepcopy(checkpoint)
        object.__setattr__(changed, "child_runs", (child, UncreatedGraphRun(child.scope_run)))
    else:
        changed = replace(
            checkpoint,
            child_runs=() if proof == "omitted" else (UncreatedGraphRun(child.scope_run),),
            graph_inputs=tuple(
                item for item in checkpoint.graph_inputs if item.coordinate.scope_run != child.scope_run
            ),
            publications=tuple(
                item for item in checkpoint.publications if item.coordinate.activation.scope_run != child.scope_run
            ),
        )
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError):
        await nested_graph(calls).run(recovery=GraphRecovery(changed, DurableGraphCommit(STRING_CODEC, store)))
    assert len(store.requests) == committed
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_false_negative_read_cannot_reexecute_an_existing_child() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: bool(request.scope) and request.candidate_state.superstep == 1
    with pytest.raises(OSError):
        await nested_graph(calls).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    checkpoint = store.checkpoint()
    child = checkpoint.child_runs[0]
    assert isinstance(child, ScopedStateBinding)
    changed = replace(
        checkpoint,
        child_runs=(UncreatedGraphRun(child.scope_run),),
        graph_inputs=tuple(item for item in checkpoint.graph_inputs if item.coordinate.scope_run != child.scope_run),
        publications=(),
    )
    store.reopen()
    with pytest.raises(ValueError, match="same commit key has different content"):
        await nested_graph(calls).run(recovery=GraphRecovery(changed, DurableGraphCommit(STRING_CODEC, store)))
    assert store.states[child.scope_run] == child.state
    assert calls == ["first"]


@pytest.mark.parametrize("coordinate", [None, ScopeRunCoordinate((), GraphRunId("root"))])
def test_uncreated_evidence_is_only_for_a_typed_nested_scope(coordinate: ScopeRunCoordinate | None) -> None:
    with pytest.raises(Graph.SnapshotMismatchError, match="nested scope-run coordinate"):
        UncreatedGraphRun(cast(ScopeRunCoordinate, coordinate))


@pytest.mark.asyncio
async def test_uncreated_read_is_readmitted_at_recovery_boundary() -> None:
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: bool(request.scope)
    with pytest.raises(OSError):
        await nested_graph([]).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    coordinate = child_scope_run(root_scope_run(GraphRunId("run")), 0, GraphNodeId("child"))
    recovery = GraphRecovery(
        deepcopy(store.checkpoint(child_reads=(coordinate,))), DurableGraphCommit(STRING_CODEC, store)
    )
    object.__setattr__(recovery.checkpoint.child_runs[0], "scope_run", root_scope_run(GraphRunId("run")))
    with pytest.raises(Graph.SnapshotMismatchError, match="child states"):
        await nested_graph([]).run(recovery=recovery)


def sibling_family(calls: list[str]) -> Graph[str]:
    parent = Graph[str]("persistent-siblings")

    async def leaf(values: Graph.Values[str]) -> Graph.Values[str]:
        calls.append(values["value"])
        return values

    for name in ("left", "right"):
        child = Graph[str](f"persistent-{name}")
        child.add_node("leaf", leaf, inputs={"value": child.graph_input("value", str)}, outputs={"value": str})
        child.set_outputs({"value": child.output_ref("leaf", "value")})
        parent.add_node(name, child, inputs={"value": parent.graph_input(name, str)})
    parent.add_join(("left", "right"), Graph.END)
    parent.set_outputs({name: parent.output_ref(name, "value") for name in ("left", "right")})
    return parent


@pytest.mark.asyncio
async def test_partial_fence_preserves_uncreated_sibling_evidence() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: request.scope == ("right",)
    with pytest.raises(OSError):
        await sibling_family(calls).run(
            Graph.values(left="left", right="right"),
            run_id="run",
            max_parallel_tasks=1,
            commit=DurableGraphCommit(STRING_CODEC, store),
        )
    right = child_scope_run(root_scope_run(GraphRunId("run")), 0, GraphNodeId("right"))
    store.reopen()
    for coordinate, state in tuple(store.states.items()):
        claimed = reduce_graph_run(state, project_claim_command(state, GraphExecutionAttemptId("old-owner"), None))
        await store(
            GraphPersistenceCommit(
                coordinate.scope,
                state.revision,
                claimed,
                GraphPersistenceWriteSet(GraphCommitKey(claimed.run_id, claimed.revision), (), ()),
            )
        )
    checkpoint = store.checkpoint(child_reads=(right,))
    left = checkpoint.child_runs[0]
    assert isinstance(left, ScopedStateBinding)
    graph = sibling_family(calls)
    commit = DurableGraphCommit(STRING_CODEC, store)
    store.fail_when = lambda request: request.scope == ("left",)
    with pytest.raises(Graph.Error) as raised:
        await graph.run(
            recovery=GraphRecovery(checkpoint, commit),
            max_parallel_tasks=1,
        )
    partial = cast(Graph.PartialCommitError[str], raised.value)
    assert isinstance(partial, Graph.PartialCommitError)
    assert isinstance(partial.cause, OSError)
    assert partial.failed_scope == ("left",)
    assert right not in store.states
    assert store.states[left.scope_run] == left.state
    assert partial.state == store.states[root_scope_run(GraphRunId("run"))]
    assert calls == []
    store.reopen()
    completed = await graph.run(
        state=partial.state,
        continuation=partial.continuation,
        max_parallel_tasks=1,
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.outputs["left"] == "left"
    assert completed.outputs["right"] == "right"
    assert calls == ["left", "right"]

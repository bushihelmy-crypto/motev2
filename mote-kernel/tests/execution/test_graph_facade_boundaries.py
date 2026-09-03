from collections.abc import Callable, Iterator, Mapping
from typing import Protocol, cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.node import NodeCallable
from mote_kernel.execution.graph.ports import GraphInputRef, NodeOutputRef
from mote_kernel.state.graph_state import GraphRunState, StartGraphRun


class LostStartError(RuntimeError):
    pass


class RuntimeRun(Protocol):
    async def __call__(
        self,
        values: Graph.Values[str] | None = None,
        /,
        *,
        state: Graph.State | None = None,
        continuation: Graph.Continuation[str] | None = None,
        resume: tuple[Graph.ResumeAction[str], ...] = (),
        run_id: str | None = None,
    ) -> Graph.Result[str]: ...


class RuntimeAddNode(Protocol):
    def __call__(
        self,
        node_id: str,
        operation: NodeCallable[str] | Graph[str],
        *,
        inputs: Mapping[str, GraphInputRef[str] | NodeOutputRef | type[str]],
        outputs: Mapping[str, type[str] | GraphInputRef[str] | NodeOutputRef] | None = None,
        resources: tuple[str, ...] = (),
    ) -> Graph[str]: ...


async def empty(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values()


async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
    return Graph.failure("failed")


def encode_empty(_values: Graph.Values[str]) -> bytes:
    return b""


def decode_empty(_payload: bytes) -> Graph.Values[str]:
    return Graph.values()


def interrupted_resume(
    graph: Graph[str],
    paused: Graph.AwaitingResumeResult[str],
    node_id: str,
    *,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[str]:
    matches = tuple(
        interrupt for interrupt in paused.interrupts if interrupt.scope == scope and interrupt.node_id == node_id
    )
    assert len(matches) == 1
    return graph.resume_interrupted(
        node_id,
        matches[0].interrupt_id,
        Graph.values(),
        scope=scope,
    )


def test_builder_local_failures_leave_a_clean_retry_surface() -> None:
    graph = Graph[str]("facade.builder-local-failures")
    add_node = cast(RuntimeAddNode, graph.add_node)

    with pytest.raises(Graph.ValidationError, match="tuple"):
        graph.add_node(
            "bad-resources",
            empty,
            inputs={},
            outputs={},
            resources=cast(tuple[str, ...], ["resource"]),
        )
    with pytest.raises(Graph.ValidationError, match="must be callable"):
        add_node("bad-operation", cast(NodeCallable[str], 1), inputs={}, outputs={})
    with pytest.raises(Graph.ValidationError, match="explicit outputs"):
        add_node("missing-outputs", empty, inputs={})

    child = Graph[str]("facade.builder-local-failures.child")
    child.add_node("leaf", empty, inputs={}, outputs={})
    child.set_outputs({})
    with pytest.raises(Graph.ValidationError, match="do not declare"):
        add_node("bad-child", child, inputs={}, outputs={})

    graph.add_node("node", empty, inputs={}, outputs={})
    graph.set_outputs({})
    with pytest.raises(Graph.ValidationError, match="exactly once"):
        graph.set_outputs({})

    codec_graph = Graph[str]("facade.builder-local-failures.codec")
    with pytest.raises(Graph.ValidationError, match="must be callable"):
        codec_graph.set_resume_codec(
            "codec",
            1,
            cast(Callable[[Graph.Values[str]], bytes], 1),
            lambda _payload: Graph.values(),
        )


class ReentrantOutputs(Mapping[str, GraphInputRef[str] | NodeOutputRef]):
    def __init__(self, graph: Graph[str]) -> None:
        self._graph = graph
        self._entered = False

    def __getitem__(self, key: str) -> GraphInputRef[str] | NodeOutputRef:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        if not self._entered:
            self._entered = True
            self._graph.set_outputs({})
        return iter(())

    def __len__(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_stale_builder_candidate_cannot_overwrite_a_reentrant_commit() -> None:
    graph = Graph[str]("facade.reentrant-builder")
    graph.add_node("node", empty, inputs={}, outputs={})

    with pytest.raises(Graph.ValidationError, match="changed before its atomic replacement"):
        graph.set_outputs(ReentrantOutputs(graph))

    result = await graph.run(Graph.values())
    assert isinstance(result, Graph.CompletedResult)


@pytest.mark.asyncio
async def test_shared_child_definition_freezes_once_and_recursive_composition_is_rejected() -> None:
    calls = 0

    async def leaf(_values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal calls
        calls += 1
        return Graph.values()

    child = Graph[str]("facade.shared-child")
    child.add_node("leaf", leaf, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("facade.shared-parent")
    parent.add_node("left", child, inputs={})
    parent.add_node("right", child, inputs={})
    parent.set_outputs({})

    completed = await parent.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    assert calls == 2
    with pytest.raises(Graph.ValidationError, match="immutable"):
        child.add_node("late", empty, inputs={}, outputs={})

    recursive = Graph[str]("facade.recursive")
    recursive.add_node("self", recursive, inputs={})
    recursive.set_outputs({})
    with pytest.raises(Graph.ValidationError, match="recursively"):
        await recursive.run(Graph.values())


@pytest.mark.asyncio
async def test_resume_dispatch_rejects_non_tuple_noncanonical_and_unknown_scope() -> None:
    async def interrupt(_values: Graph.Values[str]) -> Graph.InterruptOutcome:
        return Graph.interrupt(b"question")

    graph = Graph[str]("facade.resume-dispatch")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("a", interrupt, inputs={}, outputs={})
    graph.add_node("b", interrupt, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    run = cast(RuntimeRun, graph.run)
    resume_a = interrupted_resume(graph, paused, "a")
    resume_b = interrupted_resume(graph, paused, "b")

    with pytest.raises(Graph.SnapshotMismatchError, match="supplied as a tuple"):
        await run(
            state=paused.state,
            continuation=paused.continuation,
            resume=cast(tuple[Graph.ResumeAction[str], ...], [resume_a]),
        )
    with pytest.raises(Graph.SnapshotMismatchError, match="canonical scope/node order"):
        await run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(resume_b, resume_a),
        )
    with pytest.raises(Graph.SnapshotMismatchError, match="not one current nested activation"):
        await run(
            state=paused.state,
            continuation=paused.continuation,
            resume=(
                graph.resume_interrupted(
                    "leaf",
                    paused.interrupts[0].interrupt_id,
                    Graph.values(),
                    scope=("unknown",),
                ),
            ),
        )
    with pytest.raises(Graph.SnapshotMismatchError, match="resume scope must be a tuple"):
        graph.resume_interrupted(
            "a",
            paused.interrupts[0].interrupt_id,
            Graph.values(),
            scope=cast(tuple[str, ...], ["nested"]),
        )


@pytest.mark.asyncio
async def test_resume_scope_requires_the_current_child_snapshot() -> None:
    child = Graph[str]("facade.missing-resume-child.child")
    child.add_node("leaf", fail, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("facade.missing-resume-child.parent")
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    captured: GraphRunState | None = None

    async def lose_start(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if isinstance(transition.command, StartGraphRun):
            captured = transition.candidate_state
            raise LostStartError
        return transition.candidate_state

    with pytest.raises(LostStartError):
        await parent.run(Graph.values(), commit=lose_start)
    assert captured is not None

    with pytest.raises(Graph.SnapshotMismatchError, match="does not contain one state"):
        await parent.run(
            state=captured,
            resume=(
                parent.resume_interrupted(
                    "leaf",
                    "missing-interrupt",
                    Graph.values(),
                    scope=("child",),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_new_run_runtime_dispatch_rejects_state_resume_inputs() -> None:
    graph = Graph[str]("facade.new-run-dispatch")
    graph.add_node("node", empty, inputs={}, outputs={})
    graph.set_outputs({})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    run = cast(RuntimeRun, graph.run)

    with pytest.raises(Graph.SnapshotMismatchError, match="cannot carry state"):
        await run(Graph.values(), state=completed.state)


@pytest.mark.asyncio
async def test_state_run_runtime_dispatch_rejects_explicit_values_before_compilation() -> None:
    completed_graph = Graph[str]("facade.state-dispatch.completed")
    completed_graph.add_node("node", empty, inputs={}, outputs={})
    completed_graph.set_outputs({})
    completed = await completed_graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)

    uncompiled = Graph[str]("facade.state-dispatch.uncompiled")
    uncompiled.add_node("node", empty, inputs={}, outputs={})
    run = cast(RuntimeRun, uncompiled.run)
    with pytest.raises(Graph.SnapshotMismatchError, match="do not accept values"):
        await run(None, state=completed.state)

    uncompiled.set_outputs({})
    result = await uncompiled.run(Graph.values())
    assert isinstance(result, Graph.CompletedResult)

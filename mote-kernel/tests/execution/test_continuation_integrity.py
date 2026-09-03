from dataclasses import replace
from typing import Protocol, cast

import pytest
from tests.execution.engine.factories import compiled_graph, running_state

from mote_kernel.execution import Graph
from mote_kernel.execution.family_driver import admit_continued_root, project_graph_result
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, FrameKind, normalize_output_declarations
from mote_kernel.execution.graph.values import (
    GraphInputFrame,
    NamedValue,
    _make_graph_input_frame,
    _make_graph_output_view,
    _make_node_input_frame,
    _make_node_output_frame,
)
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.result import AbortedGraph
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    AdmittedResumeInput,
    ChildStateBinding,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    ContinuationSnapshot,
    ExecutionPublicationProvenance,
    ScopedFrameIndex,
    _admit_continuation,
    _CompiledFamilyIdentity,
)
from mote_kernel.state.graph_state import (
    AbortGraphRun,
    GraphAbortReason,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphFrontierState,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    PendingGraphNode,
    SettleGraphNode,
    UseStepRequestInput,
    reduce_graph_run,
)


class ContinuationLayout(Protocol):
    _snapshot: ContinuationSnapshot[str]

    def reveal(self) -> ContinuationSnapshot[str]:
        return self._snapshot

    def install(self, snapshot: ContinuationSnapshot[str]) -> None:
        object.__setattr__(self, "_snapshot", snapshot)


class ContinuationEditor:
    def __init__(self, continuation: ContinuationLayout) -> None:
        self._continuation = continuation

    def reveal(self) -> ContinuationSnapshot[str]:
        return ContinuationLayout.reveal(self._continuation)

    def install(self, snapshot: ContinuationSnapshot[str]) -> None:
        ContinuationLayout.install(self._continuation, snapshot)


class ForeignSeal:
    pass


class ForeignFamily:
    pass


class ContinuationAdmission(Protocol):
    def admit_snapshot(
        self,
        seal: ForeignSeal,
        family: ForeignFamily,
        state: Graph.State,
    ) -> None: ...


def test_continuation_adapter_rejects_a_foreign_runtime_value() -> None:
    state = running_state()
    continuation = cast(Graph.Continuation[str], object())

    with pytest.raises(Graph.SnapshotMismatchError, match="admitted by their Graph owner"):
        _admit_continuation(_CompiledFamilyIdentity(), state, continuation)


class LostSettlementError(RuntimeError):
    pass


async def empty(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values()


def encode_empty(_value: Graph.Values[str]) -> bytes:
    return b""


def decode_empty(_payload: bytes) -> Graph.Values[str]:
    return Graph.values()


def resume_empty_interrupt(
    graph: Graph[str],
    paused: Graph.AwaitingResumeResult[str],
    node_id: str,
) -> Graph.ResumeAction[str]:
    interrupt = next(view for view in paused.interrupts if view.node_id == node_id)
    return graph.resume_interrupted(node_id, interrupt.interrupt_id, Graph.values())


async def _completed_empty(definition_id: str) -> tuple[Graph[str], Graph.CompletedResult[str]]:
    graph = Graph[str](definition_id)
    graph.add_node("node", empty, inputs={}, outputs={})
    graph.set_outputs({})
    result = await graph.run(Graph.values())
    assert isinstance(result, Graph.CompletedResult)
    return graph, result


async def _completed_output_publication(
    definition_id: str,
) -> tuple[Graph[str], Graph.CompletedResult[str], ConfirmedPublication[str]]:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str](definition_id)
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    graph.set_outputs({"value": Graph.node_output("source", "value")})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    publication = _layout(completed.continuation).reveal().frames.publications[0]
    return graph, completed, publication


async def _completed_publication(definition_id: str) -> tuple[Graph[str], Graph.CompletedResult[str]]:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str](definition_id)
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    return graph, completed


async def _completed_parallel_children(definition_id: str) -> tuple[Graph[str], Graph.CompletedResult[str]]:
    left = Graph[str](f"{definition_id}.left")
    left.add_node("leaf", empty, inputs={}, outputs={})
    left.set_outputs({})
    right = Graph[str](f"{definition_id}.right")
    right.add_node("leaf", empty, inputs={}, outputs={})
    right.set_outputs({})
    parent = Graph[str](definition_id)
    parent.add_node("left", left, inputs={})
    parent.add_node("right", right, inputs={})
    parent.set_outputs({})
    completed = await parent.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    return parent, completed


def _layout(continuation: Graph.Continuation[str]) -> ContinuationEditor:
    return ContinuationEditor(cast(ContinuationLayout, continuation))


async def _assert_continuation_rejected_without_mutation(
    graph: Graph[str],
    completed: Graph.CompletedResult[str],
    snapshot: ContinuationSnapshot[str],
    message: str,
) -> None:
    state = completed.state
    state_before = replace(state)
    continuation = completed.continuation

    with pytest.raises(Graph.SnapshotMismatchError) as raised:
        await graph.run(state=state, continuation=continuation)

    assert str(raised.value) == message
    assert raised.value.__cause__ is None
    assert completed.state == state_before
    assert _layout(continuation).reveal() is snapshot


async def _recovered_nested() -> tuple[Graph[str], Graph.CompletedResult[str]]:
    captured: GraphRunState | None = None

    async def lose_root_settlement(transition: Graph.Transition[str], /) -> Graph.State:
        nonlocal captured
        if transition.scope == () and isinstance(transition.command, SettleGraphNode):
            captured = transition.candidate_state
            raise LostSettlementError
        return transition.candidate_state

    child = Graph[str]("continuation.recovered-child")
    child.add_node("leaf", empty, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("continuation.recovered-parent")
    parent.add_node("producer", empty, inputs={}, outputs={})
    parent.add_node("child", child, inputs={})
    parent.add_edge("producer", "child")
    parent.set_outputs({})

    with pytest.raises(LostSettlementError):
        await parent.run(Graph.values(), commit=lose_root_settlement)
    assert captured is not None
    recovered = await parent.run(state=captured)
    assert isinstance(recovered, Graph.CompletedResult)
    return parent, recovered


@pytest.mark.asyncio
async def test_continuation_admission_rejects_a_foreign_seal() -> None:
    _graph, completed = await _completed_empty("continuation.foreign-seal")
    admission = cast(ContinuationAdmission, completed.continuation)

    with pytest.raises(Graph.SnapshotMismatchError, match="admitted by their Graph owner"):
        admission.admit_snapshot(ForeignSeal(), ForeignFamily(), completed.state)


@pytest.mark.asyncio
async def test_result_projection_rejects_an_aborted_boundary_without_canonical_abort() -> None:
    graph = compiled_graph("a")
    running = running_state()
    aborted = reduce_graph_run(running, AbortGraphRun(running.revision, GraphAbortReason("aborted")))
    identity = _CompiledFamilyIdentity()
    root, evidence_reader = await admit_continued_root(
        graph,
        aborted,
        (),
        ScopedFrameIndex(),
        ExecutionLimits(),
        None,
        (),
        (),
        identity,
        recovered=True,
    )
    object.__setattr__(root.state, "abort", None)

    with pytest.raises(Graph.SnapshotMismatchError, match="missing its canonical abort"):
        project_graph_result(
            graph,
            identity,
            root,
            evidence_reader,
            AbortedGraph(),
            recovered=True,
        )


@pytest.mark.asyncio
async def test_complete_continuation_rejects_duplicate_frame_coordinates() -> None:
    graph, completed = await _completed_empty("continuation.duplicate-frame")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    layout.install(
        replace(
            snapshot,
            frames=replace(snapshot.frames, graph_inputs=(graph_input, graph_input)),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="not unique and canonical"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_descending_frame_coordinates() -> None:
    graph, completed = await _completed_parallel_children("continuation.descending-inputs")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    assert len(snapshot.frames.graph_inputs) >= 2
    tampered = replace(
        snapshot,
        frames=replace(
            snapshot.frames,
            graph_inputs=tuple(reversed(snapshot.frames.graph_inputs)),
        ),
    )
    layout.install(tampered)

    await _assert_continuation_rejected_without_mutation(
        graph,
        completed,
        tampered,
        "continuation graph input coordinates are not unique and canonical",
    )


@pytest.mark.asyncio
async def test_continuation_validation_keeps_shape_before_canonicality_precedence() -> None:
    graph, completed = await _completed_publication("continuation.shape-before-canonicality")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    publication = snapshot.frames.publications[0]
    malformed = cast(ConfirmedPublication[str], publication.coordinate)
    tampered = replace(
        snapshot,
        frames=replace(
            snapshot.frames,
            graph_inputs=(graph_input, graph_input),
            publications=(malformed,),
        ),
    )
    layout.install(tampered)

    await _assert_continuation_rejected_without_mutation(
        graph,
        completed,
        tampered,
        "continuation publication segment contains a malformed record",
    )


@pytest.mark.asyncio
async def test_continuation_validation_keeps_canonicality_before_content_precedence() -> None:
    graph, completed = await _completed_publication("continuation.canonicality-before-content")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    foreign = replace(
        graph_input,
        coordinate=replace(
            graph_input.coordinate,
            scope_run=ScopeRunCoordinate((), GraphRunId("foreign-run")),
        ),
    )
    publication = snapshot.frames.publications[0]
    tampered = replace(
        snapshot,
        frames=replace(
            snapshot.frames,
            graph_inputs=(foreign,),
            publications=(publication, publication),
        ),
    )
    layout.install(tampered)

    await _assert_continuation_rejected_without_mutation(
        graph,
        completed,
        tampered,
        "continuation publication coordinates are not unique and canonical",
    )


@pytest.mark.asyncio
async def test_continuation_validation_keeps_canonical_segment_order() -> None:
    graph, completed = await _completed_parallel_children("continuation.canonical-segment-order")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    assert len(snapshot.frames.graph_inputs) >= 2
    assert len(snapshot.frames.publications) >= 2
    tampered = replace(
        snapshot,
        frames=replace(
            snapshot.frames,
            graph_inputs=tuple(reversed(snapshot.frames.graph_inputs)),
            publications=tuple(reversed(snapshot.frames.publications)),
        ),
    )
    layout.install(tampered)

    await _assert_continuation_rejected_without_mutation(
        graph,
        completed,
        tampered,
        "continuation graph input coordinates are not unique and canonical",
    )


@pytest.mark.asyncio
async def test_recovered_continuation_rejects_noncanonical_frame_coordinates() -> None:
    graph, completed = await _recovered_nested()
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    assert len(snapshot.frames.publications) >= 2
    tampered = replace(
        snapshot,
        frames=replace(
            snapshot.frames,
            publications=tuple(reversed(snapshot.frames.publications)),
        ),
    )
    layout.install(tampered)

    await _assert_continuation_rejected_without_mutation(
        graph,
        completed,
        tampered,
        "continuation publication coordinates are not unique and canonical",
    )


@pytest.mark.asyncio
async def test_complete_continuation_rejects_descending_resume_input_coordinates() -> None:
    left_calls = 0
    right_calls = 0

    async def interrupt_left_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal left_calls
        left_calls += 1
        if left_calls == 1:
            return Graph.interrupt(b"left")
        return Graph.success(Graph.values())

    async def interrupt_right_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal right_calls
        right_calls += 1
        if right_calls == 1:
            return Graph.interrupt(b"right")
        return Graph.success(Graph.values())

    graph = Graph[str]("continuation.descending-resume-inputs")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("left", interrupt_left_once, inputs={}, outputs={})
    graph.add_node("right", interrupt_right_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(
            resume_empty_interrupt(graph, paused, "left"),
            resume_empty_interrupt(graph, paused, "right"),
        ),
    )
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    assert len(snapshot.frames.resume_inputs) == 2
    tampered = replace(
        snapshot,
        frames=replace(
            snapshot.frames,
            resume_inputs=tuple(reversed(snapshot.frames.resume_inputs)),
        ),
    )
    layout.install(tampered)

    await _assert_continuation_rejected_without_mutation(
        graph,
        completed,
        tampered,
        "continuation resume input coordinates are not unique and canonical",
    )


@pytest.mark.asyncio
async def test_complete_continuation_rejects_descending_child_boundary_coordinates() -> None:
    graph, completed = await _completed_parallel_children("continuation.descending-boundaries")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    assert len(snapshot.frames.child_boundaries) == 2
    tampered = replace(
        snapshot,
        frames=replace(
            snapshot.frames,
            child_boundaries=tuple(reversed(snapshot.frames.child_boundaries)),
        ),
    )
    layout.install(tampered)

    await _assert_continuation_rejected_without_mutation(
        graph,
        completed,
        tampered,
        "continuation child boundary coordinates are not unique and canonical",
    )


@pytest.mark.asyncio
async def test_complete_continuation_rejects_a_malformed_graph_input_record() -> None:
    graph, completed = await _completed_empty("continuation.malformed-input-record")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    coordinate = snapshot.frames.graph_inputs[0].coordinate
    malformed = cast(AdmittedGraphInput[str], coordinate)
    layout.install(replace(snapshot, frames=replace(snapshot.frames, graph_inputs=(malformed,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="graph input segment contains a malformed record"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_a_malformed_publication_record() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str]("continuation.malformed-publication-record")
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    coordinate = snapshot.frames.publications[0].coordinate
    malformed = cast(ConfirmedPublication[str], coordinate)
    layout.install(replace(snapshot, frames=replace(snapshot.frames, publications=(malformed,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="publication segment contains a malformed record"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_a_malformed_resume_input_record() -> None:
    calls = 0

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.interrupt(b"question")
        return Graph.success(Graph.values())

    graph = Graph[str]("continuation.malformed-resume-record")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("node", interrupt_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(resume_empty_interrupt(graph, paused, "node"),),
    )
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    coordinate = snapshot.frames.resume_inputs[0].coordinate
    malformed = cast(AdmittedResumeInput[str], coordinate)
    layout.install(replace(snapshot, frames=replace(snapshot.frames, resume_inputs=(malformed,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="resume input segment contains a malformed record"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_a_malformed_child_boundary_record() -> None:
    child = Graph[str]("continuation.malformed-boundary-child")
    child.add_node("leaf", empty, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("continuation.malformed-boundary-parent")
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    completed = await parent.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    coordinate = snapshot.frames.child_boundaries[0].coordinate
    malformed = cast(ConfirmedChildBoundary[str], coordinate)
    layout.install(replace(snapshot, frames=replace(snapshot.frames, child_boundaries=(malformed,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="child boundary segment contains a malformed record"):
        await parent.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_a_foreign_graph_input_coordinate() -> None:
    graph, completed = await _completed_empty("continuation.foreign-input")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    foreign = replace(
        graph_input,
        coordinate=replace(
            graph_input.coordinate,
            scope_run=ScopeRunCoordinate((), GraphRunId("foreign-run")),
        ),
    )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, graph_inputs=(foreign,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="unknown scoped run"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_a_wrong_graph_input_descriptor() -> None:
    graph, completed = await _completed_empty("continuation.input-descriptor")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    wrong = replace(
        graph_input,
        coordinate=replace(
            graph_input.coordinate,
            descriptor=replace(
                graph_input.coordinate.descriptor,
                owner_ordinal=graph_input.coordinate.descriptor.owner_ordinal + 1,
            ),
        ),
    )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, graph_inputs=(wrong,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="descriptor does not match"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_readmits_graph_input_frame_names_and_values() -> None:
    source = Graph.graph_input("value", str)
    graph = Graph[str]("continuation.input-frame-content")
    graph.add_node("node", empty, inputs={"value": source}, outputs={})
    graph.set_outputs({})
    completed = await graph.run(Graph.values(value="input"))
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    wrong_frame = cast(
        GraphInputFrame[str],
        _make_graph_input_frame(
            Graph.values(value=7),
            normalize_output_declarations({"value": int}),
        ),
    )
    layout.install(
        replace(
            snapshot,
            frames=replace(snapshot.frames, graph_inputs=(replace(graph_input, frame=wrong_frame),)),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="graph input frame does not match"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_the_wrong_graph_input_frame_nominal() -> None:
    graph, completed = await _completed_empty("continuation.input-frame-nominal")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    wrong_frame = cast(GraphInputFrame[str], _make_node_input_frame((), normalize_output_declarations({})))
    layout.install(
        replace(
            snapshot,
            frames=replace(snapshot.frames, graph_inputs=(replace(graph_input, frame=wrong_frame),)),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="graph input frame does not match"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_an_inconsistent_publication() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str]("continuation.publication")
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    publication = snapshot.frames.publications[0]
    layout.install(
        replace(
            snapshot,
            frames=replace(
                snapshot.frames,
                publications=(replace(publication, acknowledged_revision=0),),
            ),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="publication has inconsistent coordinates"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_invalid_execution_publication_provenance() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str]("continuation.publication-provenance")
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    publication = snapshot.frames.publications[0]
    malformed = replace(
        publication,
        provenance=ExecutionPublicationProvenance(GraphExecutionToken(0, GraphExecutionAttemptId("invalid"))),
    )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, publications=(malformed,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="execution provenance"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_readmits_publication_frame_content() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str]("continuation.publication-frame-content")
    graph.add_node("source", publish, inputs={}, outputs={"value": str})
    graph.set_outputs({})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    publication = snapshot.frames.publications[0]
    wrong_frame = _make_node_output_frame(
        Graph.values(other="published"),
        normalize_output_declarations({"other": str}),
    )
    layout.install(
        replace(
            snapshot,
            frames=replace(snapshot.frames, publications=(replace(publication, frame=wrong_frame),)),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="publication frame does not match"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_valid_historical_publication_continuation_survives_completion() -> None:
    graph, completed, publication = await _completed_output_publication("continuation.valid-publication")

    repeated = await graph.run(state=completed.state, continuation=completed.continuation)

    assert isinstance(repeated, Graph.CompletedResult)
    assert repeated.outputs["value"] == "published"
    assert publication.coordinate.activation.node_id == GraphNodeId("source")
    assert all(node.node_id != GraphNodeId("source") for node in completed.state.frontier.nodes)


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["provenance", "revision", "descriptor", "lineage", "frame"])
async def test_publication_continuation_rejects_each_integrity_violation(tamper: str) -> None:
    graph, completed, publication = await _completed_output_publication(f"continuation.publication-{tamper}")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    malformed = publication
    if tamper == "provenance":
        malformed = replace(
            malformed,
            provenance=cast(ExecutionPublicationProvenance, object()),
        )
    elif tamper == "revision":
        malformed = replace(malformed, acknowledged_revision=completed.state.revision + 1)
    elif tamper == "descriptor":
        malformed = replace(
            malformed,
            coordinate=replace(
                malformed.coordinate,
                descriptor=FrameDescriptorIdentity(
                    GraphDefinitionId("foreign"), GraphDefinitionVersion(1), FrameKind.NODE_OUTPUT, 0
                ),
            ),
        )
    elif tamper == "lineage":
        malformed = replace(
            malformed,
            coordinate=replace(
                malformed.coordinate,
                activation=replace(
                    malformed.coordinate.activation,
                    scope_run=ScopeRunCoordinate((GraphNodeId("unknown"),), GraphRunId("unknown-run")),
                ),
            ),
        )
    else:
        malformed = replace(
            malformed,
            frame=_make_node_output_frame(
                Graph.values(other="published"),
                normalize_output_declarations({"other": str}),
            ),
        )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, publications=(malformed,))))

    with pytest.raises(Graph.SnapshotMismatchError):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_an_inconsistent_resume_input() -> None:
    calls = 0

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.interrupt(b"question")
        return Graph.success(Graph.values())

    graph = Graph[str]("continuation.resume-input")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("node", interrupt_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(resume_empty_interrupt(graph, paused, "node"),),
    )
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    admitted = snapshot.frames.resume_inputs[0]
    wrong = replace(
        admitted,
        coordinate=replace(
            admitted.coordinate,
            descriptor=replace(
                admitted.coordinate.descriptor,
                owner_ordinal=admitted.coordinate.descriptor.owner_ordinal + 1,
            ),
        ),
    )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, resume_inputs=(wrong,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="resume input has inconsistent coordinates"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_readmits_resume_input_frame_content() -> None:
    calls = 0

    async def interrupt_once(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.interrupt(b"question")
        return Graph.success(Graph.values())

    graph = Graph[str]("continuation.resume-frame-content")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("node", interrupt_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(resume_empty_interrupt(graph, paused, "node"),),
    )
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    admitted = snapshot.frames.resume_inputs[0]
    wrong_frame = _make_node_input_frame(
        (NamedValue("extra", "input"),),
        normalize_output_declarations({"extra": str}),
    )
    layout.install(
        replace(
            snapshot,
            frames=replace(
                snapshot.frames,
                resume_inputs=(replace(admitted, frame=wrong_frame),),
            ),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="resume input frame does not match"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_an_inconsistent_child_boundary() -> None:
    child = Graph[str]("continuation.boundary-child")
    child.add_node("leaf", empty, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("continuation.boundary-parent")
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    completed = await parent.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    boundary = snapshot.frames.child_boundaries[0]
    wrong = replace(
        boundary,
        coordinate=replace(
            boundary.coordinate,
            descriptor=replace(
                boundary.coordinate.descriptor,
                owner_ordinal=boundary.coordinate.descriptor.owner_ordinal + 1,
            ),
        ),
    )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, child_boundaries=(wrong,))))

    with pytest.raises(Graph.SnapshotMismatchError, match="child boundary has inconsistent coordinates"):
        await parent.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_readmits_child_boundary_frame_content() -> None:
    child = Graph[str]("continuation.boundary-frame-child")
    child.add_node("leaf", empty, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("continuation.boundary-frame-parent")
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    completed = await parent.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    boundary = snapshot.frames.child_boundaries[0]
    wrong_frame = _make_graph_output_view(
        (NamedValue("extra", "output"),),
        normalize_output_declarations({"extra": str}),
    )
    layout.install(
        replace(
            snapshot,
            frames=replace(snapshot.frames, child_boundaries=(replace(boundary, frame=wrong_frame),)),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="child boundary frame does not match"):
        await parent.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_requires_every_scoped_graph_input() -> None:
    graph, completed = await _completed_empty("continuation.missing-input")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    layout.install(replace(snapshot, frames=replace(snapshot.frames, graph_inputs=())))

    with pytest.raises(Graph.SnapshotMismatchError, match="retain every scoped graph input"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_requires_each_current_success_publication() -> None:
    async def succeed(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"pause")

    graph = Graph[str]("continuation.current-publication")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("success", succeed, inputs={}, outputs={})
    graph.add_node("interrupt", interrupt, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    layout = _layout(paused.continuation)
    snapshot = layout.reveal()
    assert snapshot.frames.publications
    layout.install(replace(snapshot, frames=replace(snapshot.frames, publications=())))

    with pytest.raises(Graph.SnapshotMismatchError, match="current success publication"):
        await graph.run(state=paused.state, continuation=paused.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_requires_a_pending_node_input_source() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"pause")

    graph = Graph[str]("continuation.pending-input")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("producer", publish, inputs={}, outputs={"value": str})
    graph.add_node(
        "consumer",
        interrupt,
        inputs={"value": Graph.node_output("producer", "value")},
        outputs={},
    )
    graph.add_edge("producer", "consumer")
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    node = paused.state.frontier.nodes[0]
    pending = replace(
        paused.state,
        frontier=GraphFrontierState((replace(node, settlement=PendingGraphNode(UseStepRequestInput())),)),
    )
    layout = _layout(paused.continuation)
    snapshot = layout.reveal()
    layout.install(
        replace(
            snapshot,
            root_state=pending,
            frames=replace(snapshot.frames, publications=()),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="current node input source"):
        await graph.run(state=pending, continuation=paused.continuation)


@pytest.mark.asyncio
async def test_ordinary_input_consumer_settles_before_nested_child_awaits_resume() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    async def interrupt(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.interrupt(b"park child")

    consumed = 0

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        nonlocal consumed
        consumed += 1
        return Graph.values()

    child = Graph[str]("continuation.parked-child")
    child.set_resume_codec("empty", 1, encode_empty, decode_empty)
    child.add_node("leaf", interrupt, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("continuation.parked-parent")
    parent.add_node("producer", publish, inputs={}, outputs={"value": str})
    parent.add_node("child", child, inputs={})
    parent.add_node(
        "consumer",
        consume,
        inputs={"value": Graph.node_output("producer", "value")},
        outputs={},
    )
    parent.add_edge("producer", "child")
    parent.add_edge("producer", "consumer")
    parent.set_outputs({})
    paused = await parent.run(Graph.values(), max_parallel_tasks=1)
    assert isinstance(paused, Graph.AwaitingResumeResult)
    assert consumed == 1

    repeated = await parent.run(
        state=paused.state,
        continuation=paused.continuation,
        max_parallel_tasks=1,
    )
    assert isinstance(repeated, Graph.AwaitingResumeResult)
    assert consumed == 1


@pytest.mark.asyncio
async def test_complete_continuation_requires_historical_graph_output_publication() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    graph = Graph[str]("continuation.completed-output")
    graph.add_node("producer", publish, inputs={}, outputs={"value": str})
    graph.add_node("final", empty, inputs={}, outputs={})
    graph.add_edge("producer", "final")
    graph.set_outputs({"value": Graph.node_output("producer", "value")})
    completed = await graph.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    retained = tuple(
        publication
        for publication in snapshot.frames.publications
        if publication.coordinate.activation.node_id != GraphNodeId("producer")
    )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, publications=retained)))

    with pytest.raises(Graph.SnapshotMismatchError, match="completed graph output"):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_requires_completed_child_boundary() -> None:
    child = Graph[str]("continuation.missing-boundary-child")
    child.add_node("leaf", empty, inputs={}, outputs={})
    child.set_outputs({})
    parent = Graph[str]("continuation.missing-boundary-parent")
    parent.add_node("child", child, inputs={})
    parent.set_outputs({})
    completed = await parent.run(Graph.values())
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    layout.install(replace(snapshot, frames=replace(snapshot.frames, child_boundaries=())))

    with pytest.raises(Graph.SnapshotMismatchError, match="completed child boundary"):
        await parent.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_recovered_continuation_rejects_an_unknown_child_scope() -> None:
    parent, recovered = await _recovered_nested()
    layout = _layout(recovered.continuation)
    snapshot = layout.reveal()
    child = snapshot.child_states[0]
    unknown = replace(
        child,
        coordinate=ScopeRunCoordinate((GraphNodeId("unknown"),), child.coordinate.graph_run_id),
    )
    layout.install(replace(snapshot, child_states=(unknown,), frames=ScopedFrameIndex()))

    with pytest.raises(Graph.SnapshotMismatchError, match="unknown nested node"):
        await parent.run(state=recovered.state, continuation=recovered.continuation)


@pytest.mark.asyncio
async def test_recovered_continuation_readmits_existing_frame_content() -> None:
    parent, recovered = await _recovered_nested()
    layout = _layout(recovered.continuation)
    snapshot = layout.reveal()
    graph_input = snapshot.frames.graph_inputs[0]
    wrong_frame = _make_graph_input_frame(
        Graph.values(extra="malformed"),
        normalize_output_declarations({"extra": str}),
    )
    layout.install(
        replace(
            snapshot,
            frames=replace(
                snapshot.frames,
                graph_inputs=(replace(graph_input, frame=wrong_frame),),
            ),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="graph input frame does not match"):
        await parent.run(state=recovered.state, continuation=recovered.continuation)


@pytest.mark.asyncio
async def test_recovered_continuation_rejects_duplicate_child_run_coordinates() -> None:
    parent, recovered = await _recovered_nested()
    layout = _layout(recovered.continuation)
    snapshot = layout.reveal()
    child = snapshot.child_states[0]
    layout.install(replace(snapshot, child_states=(child, child), frames=ScopedFrameIndex()))

    with pytest.raises(Graph.SnapshotMismatchError, match="repeats one scoped graph run"):
        await parent.run(state=recovered.state, continuation=recovered.continuation)


@pytest.mark.asyncio
async def test_continuation_rejects_noncanonical_child_binding_order() -> None:
    parent, completed = await _completed_parallel_children("continuation.child-binding-order")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    layout.install(replace(snapshot, child_states=tuple(reversed(snapshot.child_states))))

    with pytest.raises(Graph.SnapshotMismatchError, match="canonical scoped order"):
        await parent.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_continuation_rejects_duplicate_parent_activation() -> None:
    parent, completed = await _completed_parallel_children("continuation.duplicate-parent-activation")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    left, right = snapshot.child_states
    layout.install(
        replace(
            snapshot,
            child_states=(left, replace(right, parent_activation=left.parent_activation)),
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="repeats one parent graph activation"):
        await parent.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_recovered_continuation_rejects_a_child_run_id_mismatch() -> None:
    parent, recovered = await _recovered_nested()
    layout = _layout(recovered.continuation)
    snapshot = layout.reveal()
    child = snapshot.child_states[0]
    mismatched = replace(
        child,
        coordinate=replace(child.coordinate, graph_run_id=GraphRunId("foreign-child-run")),
    )
    layout.install(replace(snapshot, child_states=(mismatched,), frames=ScopedFrameIndex()))

    with pytest.raises(Graph.SnapshotMismatchError, match="scope-run coordinate"):
        await parent.run(state=recovered.state, continuation=recovered.continuation)


@pytest.mark.asyncio
async def test_recovered_continuation_rejects_inconsistent_parent_coordinates() -> None:
    parent, recovered = await _recovered_nested()
    layout = _layout(recovered.continuation)
    snapshot = layout.reveal()
    child = snapshot.child_states[0]
    activation = child.parent_activation
    inconsistent_activation = StableActivation(
        activation.scope_run,
        activation.superstep + 1,
        activation.node_id,
    )
    inconsistent = ChildStateBinding(child.coordinate, inconsistent_activation, child.state)
    layout.install(replace(snapshot, child_states=(inconsistent,), frames=ScopedFrameIndex()))

    with pytest.raises(Graph.SnapshotMismatchError, match="inconsistent parent coordinates"):
        await parent.run(state=recovered.state, continuation=recovered.continuation)

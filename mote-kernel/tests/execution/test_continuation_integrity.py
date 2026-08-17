from dataclasses import replace
from typing import Protocol, cast

import pytest
from tests.execution.engine.factories import compiled_graph, running_state

from mote_kernel.execution import Graph
from mote_kernel.execution.family_driver import project_graph_result
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, FrameKind, canonical_nominal_type
from mote_kernel.execution.graph.values import (
    GraphInputFrame,
    NamedValue,
    _make_graph_input_frame,
    _make_graph_output_view,
    _make_node_input_frame,
    _make_node_output_frame,
)
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
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
    SkipSubstitutionProvenance,
    _new_context,
    _new_family_identity,
)
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    SettleGraphNode,
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
    def admit(
        self,
        seal: ForeignSeal,
        family: ForeignFamily,
        state: Graph.State,
    ) -> None: ...


class LostSettlementError(RuntimeError):
    pass


async def empty(_values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values()


async def _completed_empty(definition_id: str) -> tuple[Graph[str], Graph.CompletedResult[str]]:
    graph = Graph[str](definition_id)
    graph.add_node("node", empty, inputs={}, outputs={})
    graph.set_outputs({})
    result = await graph.run(Graph.values())
    assert isinstance(result, Graph.CompletedResult)
    return graph, result


async def _completed_substitution(
    definition_id: str,
) -> tuple[Graph[str], Graph.CompletedResult[str], ConfirmedPublication[str]]:
    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("declined")

    graph = Graph[str](definition_id)
    graph.add_node("source", fail, inputs={}, outputs={"value": str})
    graph.set_outputs({"value": Graph.node_output("source", "value")})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.skip_failed("source", "replacement", output=Graph.values(value="replacement")),),
    )
    assert isinstance(completed, Graph.CompletedResult)
    publication = next(
        record
        for record in _layout(completed.continuation).reveal().frames.publications
        if isinstance(record.provenance, SkipSubstitutionProvenance)
    )
    return graph, completed, publication


def _layout(continuation: Graph.Continuation[str]) -> ContinuationEditor:
    return ContinuationEditor(cast(ContinuationLayout, continuation))


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
        admission.admit(ForeignSeal(), ForeignFamily(), completed.state)


def test_result_projection_rejects_an_aborted_boundary_without_canonical_abort() -> None:
    malformed = replace(running_state(), status=GraphRunStatus.ABORTED)
    context = _new_context(
        _new_family_identity(),
        malformed,
        ScopedFrameIndex(),
        recovered=True,
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="missing its canonical abort"):
        project_graph_result(compiled_graph("a"), context, AbortedGraph())


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

    async def fail_once(_values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.failure("retry") if calls == 1 else Graph.values()

    def encode_empty(_values: Graph.Values[str]) -> bytes:
        return b""

    def decode_empty(_payload: bytes) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("continuation.malformed-resume-record")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("node", fail_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.resume_failed_with("node", Graph.values()),),
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
            (("value", canonical_nominal_type(int)),),
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
    wrong_frame = cast(GraphInputFrame[str], _make_node_input_frame((), ()))
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
        (("other", canonical_nominal_type(str)),),
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
async def test_valid_historical_substitution_continuation_survives_frontier_advance() -> None:
    graph, completed, publication = await _completed_substitution("continuation.valid-substitution")

    repeated = await graph.run(state=completed.state, continuation=completed.continuation)

    assert isinstance(repeated, Graph.CompletedResult)
    assert repeated.outputs["value"] == "replacement"
    assert publication.coordinate.activation.node_id == GraphNodeId("source")
    assert all(node.node_id != GraphNodeId("source") for node in completed.state.frontier.nodes)


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["provenance", "revision", "descriptor", "lineage", "frame"])
async def test_substitution_continuation_rejects_each_integrity_violation(tamper: str) -> None:
    graph, completed, publication = await _completed_substitution(f"continuation.substitution-{tamper}")
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    malformed = publication
    if tamper == "provenance":
        malformed = replace(
            malformed,
            provenance=cast(
                SkipSubstitutionProvenance,
                ExecutionPublicationProvenance(GraphExecutionToken(0, GraphExecutionAttemptId("invalid"))),
            ),
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
                Graph.values(other="replacement"),
                (("other", canonical_nominal_type(str)),),
            ),
        )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, publications=(malformed,))))

    with pytest.raises(Graph.SnapshotMismatchError):
        await graph.run(state=completed.state, continuation=completed.continuation)


@pytest.mark.asyncio
async def test_complete_continuation_rejects_an_inconsistent_resume_input() -> None:
    calls = 0

    async def fail_once(_values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return Graph.failure("retry")
        return Graph.values()

    graph = Graph[str]("continuation.resume-input")
    graph.add_node("node", fail_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.resume_failed("node"),),
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

    async def fail_once(_values: Graph.Values[str]) -> Graph.Values[str] | Graph.Outcome[str]:
        nonlocal calls
        calls += 1
        return Graph.failure("retry") if calls == 1 else Graph.values()

    def encode_empty(_values: Graph.Values[str]) -> bytes:
        return b""

    def decode_empty(_payload: bytes) -> Graph.Values[str]:
        return Graph.values()

    graph = Graph[str]("continuation.resume-frame-content")
    graph.set_resume_codec("empty", 1, encode_empty, decode_empty)
    graph.add_node("node", fail_once, inputs={}, outputs={})
    graph.set_outputs({})
    paused = await graph.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=(graph.resume_failed_with("node", Graph.values()),),
    )
    assert isinstance(completed, Graph.CompletedResult)
    layout = _layout(completed.continuation)
    snapshot = layout.reveal()
    admitted = snapshot.frames.resume_inputs[0]
    wrong_frame = _make_node_input_frame(
        (NamedValue("extra", "input"),),
        (("extra", canonical_nominal_type(str)),),
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
        (("extra", canonical_nominal_type(str)),),
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

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("pause")

    graph = Graph[str]("continuation.current-publication")
    graph.add_node("success", succeed, inputs={}, outputs={})
    graph.add_node("failure", fail, inputs={}, outputs={})
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
async def test_complete_continuation_validates_a_parked_ordinary_input_source() -> None:
    async def publish(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values(value="published")

    async def fail(_values: Graph.Values[str]) -> Graph.Outcome[str]:
        return Graph.failure("park child")

    async def consume(_values: Graph.Values[str]) -> Graph.Values[str]:
        return Graph.values()

    child = Graph[str]("continuation.parked-child")
    child.add_node("leaf", fail, inputs={}, outputs={})
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
    parent.set_outputs({})
    paused = await parent.run(Graph.values())
    assert isinstance(paused, Graph.AwaitingResumeResult)

    repeated = await parent.run(state=paused.state, continuation=paused.continuation)
    assert isinstance(repeated, Graph.AwaitingResumeResult)
    layout = _layout(repeated.continuation)
    snapshot = layout.reveal()
    producer_publication = next(
        publication
        for publication in snapshot.frames.publications
        if publication.coordinate.activation.node_id == GraphNodeId("producer")
    )
    retained = tuple(
        publication for publication in snapshot.frames.publications if publication is not producer_publication
    )
    layout.install(replace(snapshot, frames=replace(snapshot.frames, publications=retained)))

    with pytest.raises(Graph.SnapshotMismatchError, match="current node input source"):
        await parent.run(state=repeated.state, continuation=repeated.continuation)


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
        (("extra", canonical_nominal_type(str)),),
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

    with pytest.raises(Graph.SnapshotMismatchError, match="scoped run identity"):
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

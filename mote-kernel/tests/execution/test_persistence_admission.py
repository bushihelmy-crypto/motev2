from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Protocol, cast

import pytest
from tests.execution.engine.factories import activation_config, running_state
from tests.execution.persistence_fixtures import (
    STRING_CODEC,
    MemoryPersistence,
    capture_graph_input,
    linear_graph,
    nested_graph,
)

from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.commit import GraphCommitKey
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.persistence import (
    DurableGraphCommit,
    EncodedAgentSession,
    EncodedFrame,
    GraphCheckpoint,
    GraphPersistenceCommit,
    GraphPersistenceWriteSet,
    GraphRecovery,
    GraphSessionReceipt,
    PersistedGraphInput,
    PersistedPublication,
)
from mote_kernel.execution.run_context import (
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedStateBinding,
)
from mote_kernel.state.graph_state import (
    GraphEvidenceCommitment,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
)

PUBLICATION_CHANGES: tuple[Callable[[PersistedPublication[str]], PersistedPublication[str]], ...] = (
    lambda item: replace(item, birth=replace(item.birth, revision=0)),
    lambda item: replace(item, birth=replace(item.birth, revision=True)),
    lambda item: replace(item, birth=replace(item.birth, revision=999)),
    lambda item: replace(item, provenance=cast(ExecutionPublicationProvenance, None)),
    lambda item: replace(item, provenance=ExecutionPublicationProvenance(cast(GraphExecutionToken, None))),
    lambda item: replace(
        item, provenance=ExecutionPublicationProvenance(GraphExecutionToken(999, GraphExecutionAttemptId("attempt")))
    ),
    lambda item: replace(
        item, provenance=ExecutionPublicationProvenance(GraphExecutionToken(True, GraphExecutionAttemptId("attempt")))
    ),
    lambda item: replace(
        item, provenance=ExecutionPublicationProvenance(GraphExecutionToken(1, GraphExecutionAttemptId("")))
    ),
    lambda item: replace(item, coordinate=cast(PublicationAvailabilityCoordinate[str], None)),
    lambda item: replace(item, coordinate=replace(item.coordinate, activation=cast(StableActivation, None))),
    lambda item: replace(
        item, coordinate=replace(item.coordinate, descriptor=replace(item.coordinate.descriptor, owner_ordinal=99))
    ),
    lambda item: replace(
        item,
        coordinate=replace(
            item.coordinate, activation=replace(item.coordinate.activation, node_id=GraphNodeId("unknown"))
        ),
    ),
    lambda item: replace(
        item, coordinate=replace(item.coordinate, activation=replace(item.coordinate.activation, superstep=999))
    ),
    lambda item: replace(item, frame=replace(item.frame, codec_id="another")),
    lambda item: replace(item, frame=replace(item.frame, codec_version=2)),
)


class UnsupportedPersistedGraphInput(PersistedGraphInput[str]):
    pass


class UnsupportedPersistedPublication(PersistedPublication[str]):
    pass


class UnsupportedWriteSet(GraphPersistenceWriteSet[str]):
    pass


class UnsupportedCommit(GraphPersistenceCommit[str]):
    pass


class UnsupportedCheckpoint(GraphCheckpoint[str]):
    pass


class UnsupportedRecovery(GraphRecovery[str]):
    pass


class UnsupportedSessionReceipt(GraphSessionReceipt):
    pass


class UnsupportedDurableCommit(DurableGraphCommit[str]):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    PUBLICATION_CHANGES,
    ids=[
        "zero-birth-revision",
        "bool-birth-revision",
        "future-birth-revision",
        "provenance-type",
        "token-type",
        "future-token",
        "bool-generation",
        "attempt-id",
        "coordinate-type",
        "activation-type",
        "descriptor",
        "unknown-node",
        "future-activation",
        "codec-id",
        "codec-version",
    ],
)
async def test_publication_admission_is_before_commit_and_node_calls(
    change: Callable[[PersistedPublication[str]], PersistedPublication[str]],
) -> None:
    store = MemoryPersistence[str]()
    calls: list[str] = []
    await linear_graph(calls).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    store.unavailable = True
    with pytest.raises(Graph.SnapshotMismatchError):
        checkpoint = replace(
            checkpoint, publications=(change(checkpoint.publications[0]), *checkpoint.publications[1:])
        )
        await linear_graph(calls).run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))
    assert calls == ["first", "second"]


INPUT_CHANGES: tuple[Callable[[PersistedGraphInput[str]], PersistedGraphInput[str]], ...] = (
    lambda item: replace(item, coordinate=cast(GraphInputAvailabilityCoordinate[str], None)),
    lambda item: replace(item, coordinate=replace(item.coordinate, scope_run=cast(ScopeRunCoordinate, None))),
    lambda item: replace(
        item, coordinate=replace(item.coordinate, scope_run=ScopeRunCoordinate((), GraphRunId("other")))
    ),
    lambda item: replace(item, coordinate=replace(item.coordinate, descriptor=cast(FrameDescriptorIdentity, None))),
    lambda item: replace(item, frame=cast(object, None)),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", INPUT_CHANGES)
async def test_graph_input_coordinates_are_admitted(
    change: Callable[[PersistedGraphInput[str]], PersistedGraphInput[str]],
) -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    with pytest.raises(Graph.SnapshotMismatchError):
        checkpoint = replace(checkpoint, graph_inputs=(change(checkpoint.graph_inputs[0]),))
        await linear_graph([]).run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
async def test_duplicate_evidence_is_not_silently_deduplicated(segment: str) -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    with pytest.raises(Graph.SnapshotMismatchError, match=r"repeats one|canonical and distinct"):
        checkpoint = (
            replace(checkpoint, graph_inputs=checkpoint.graph_inputs * 2)
            if segment == "input"
            else replace(checkpoint, publications=checkpoint.publications * 2)
        )
        await linear_graph([]).run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))


@pytest.mark.asyncio
async def test_missing_running_publication_is_not_reexecuted() -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: request.candidate_state.superstep == 1
    with pytest.raises(OSError):
        await linear_graph(calls).run(
            Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    checkpoint = replace(store.checkpoint(), publications=())
    with pytest.raises(Graph.SnapshotMismatchError, match="settlement ledger"):
        await linear_graph(calls).run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))
    assert calls == ["first"]


@pytest.mark.asyncio
async def test_definition_upgrade_is_not_a_recovery_fallback() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    with pytest.raises(Graph.SnapshotMismatchError, match="identity and version"):
        await linear_graph([], version=2).run(
            recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"root_state": None},
        {"child_runs": []},
        {"child_runs": (None,)},
        {"graph_inputs": []},
        {"graph_inputs": (None,)},
        {"publications": []},
        {"publications": (None,)},
    ],
)
def test_checkpoint_rejects_untyped_segments(changes: dict[str, object]) -> None:
    checkpoint = GraphCheckpoint[str](running_state(), (), (), ())
    with pytest.raises(Graph.SnapshotMismatchError):
        replace(checkpoint, **changes)


def test_checkpoint_requires_real_root_state() -> None:
    with pytest.raises(Graph.SnapshotMismatchError):
        GraphCheckpoint[str](cast(GraphRunState, None), (), (), ())


@pytest.mark.parametrize("invalid", ["checkpoint", "configs"])
def test_recovery_requires_typed_capabilities(invalid: str) -> None:
    checkpoint = GraphCheckpoint[str](running_state(), (), (), ())
    with pytest.raises(Graph.SnapshotMismatchError):
        if invalid == "checkpoint":
            GraphRecovery[str](
                cast(GraphCheckpoint[str], None), DurableGraphCommit(STRING_CODEC, MemoryPersistence[str]())
            )
        else:
            GraphRecovery(
                checkpoint, DurableGraphCommit(STRING_CODEC, MemoryPersistence[str]()), cast(tuple[Config, ...], [])
            )


def test_recovery_requires_its_durable_commit_capability() -> None:
    checkpoint = GraphCheckpoint[str](running_state(), (), (), ())
    with pytest.raises(Graph.ValidationError, match="durable commit capability"):
        GraphRecovery(checkpoint, cast(DurableGraphCommit[str], None))


@pytest.mark.parametrize("invalid", ["coordinate", "state"])
def test_child_binding_payload_is_typed_before_lineage_access(invalid: str) -> None:
    coordinate = ScopeRunCoordinate((GraphNodeId("child"),), GraphRunId("child-run"))
    binding = ScopedStateBinding(
        cast(ScopeRunCoordinate, None) if invalid == "coordinate" else coordinate,
        cast(GraphRunState, None) if invalid == "state" else running_state(),
    )
    with pytest.raises(Graph.SnapshotMismatchError):
        GraphCheckpoint[str](running_state(), (binding,), (), ())


@pytest.mark.asyncio
async def test_persistent_decoder_requires_canonical_bytes_not_just_a_matching_commitment() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    original = checkpoint.graph_inputs[0]
    payload = b'{"value" : "input"}'
    changed = capture_graph_input(
        original.coordinate,
        EncodedFrame(original.frame.codec_id, original.frame.codec_version, payload, original.frame.config_cursor),
        original.birth,
    )
    with pytest.raises(Graph.SnapshotMismatchError, match="canonical"):
        await linear_graph([]).run(
            recovery=GraphRecovery(
                replace(
                    checkpoint,
                    root_state=replace(checkpoint.root_state, graph_input_evidence=changed.evidence),
                    graph_inputs=(changed,),
                ),
                DurableGraphCommit(STRING_CODEC, store),
            )
        )


class RecoveryRun(Protocol):
    async def __call__(
        self,
        *values: Graph.Values[str],
        recovery: GraphRecovery[str],
        state: Graph.State | None = None,
        continuation: Graph.Continuation[str] | None = None,
        run_id: str | None = None,
        activation_config: Config | None = None,
        commit: Graph.Commit[str] | None = None,
    ) -> Graph.Result[str]: ...


class MissingFieldAdmission(Protocol):
    def admit(self) -> object: ...


@pytest.mark.parametrize(
    "record_type",
    [
        PersistedGraphInput,
        PersistedPublication,
        GraphPersistenceWriteSet,
        GraphPersistenceCommit,
        GraphCheckpoint,
        GraphRecovery,
    ],
)
def test_exact_persistence_records_with_missing_fields_fail_with_snapshot_errors(
    record_type: type[object],
) -> None:
    malformed = cast(MissingFieldAdmission, object.__new__(record_type))
    with pytest.raises(Graph.SnapshotMismatchError):
        malformed.admit()


def test_encoded_frame_with_missing_fields_fails_with_a_snapshot_error() -> None:
    malformed = object.__new__(EncodedFrame)
    with pytest.raises(Graph.SnapshotMismatchError):
        EncodedFrame.admit(malformed)


@pytest.mark.parametrize(
    "record_type",
    [
        UnsupportedPersistedGraphInput,
        UnsupportedPersistedPublication,
        UnsupportedWriteSet,
        UnsupportedCommit,
        UnsupportedCheckpoint,
        UnsupportedRecovery,
    ],
)
def test_persistence_record_admission_rejects_subclasses(record_type: type[object]) -> None:
    unsupported = cast(MissingFieldAdmission, object.__new__(record_type))
    with pytest.raises(Graph.SnapshotMismatchError):
        unsupported.admit()


def test_durable_commit_admission_rejects_subclasses_and_incomplete_exact_records() -> None:
    unsupported = cast(DurableGraphCommit[str], object.__new__(UnsupportedDurableCommit))
    with pytest.raises(Graph.ValidationError, match="durable commit capability"):
        unsupported.admit()
    incomplete = cast(DurableGraphCommit[str], object.__new__(DurableGraphCommit))
    with pytest.raises(Graph.ValidationError, match="capability is malformed"):
        incomplete.admit()
    malformed = cast(DurableGraphCommit[str], object.__new__(DurableGraphCommit))
    object.__setattr__(malformed, "codec", cast(FrameCodec[str], None))
    object.__setattr__(malformed, "writer", MemoryPersistence[str]())
    with pytest.raises(Graph.ValidationError, match="typed codec and writer"):
        malformed.admit()


def test_publication_provenance_with_missing_fields_fails_at_its_owner_boundary() -> None:
    malformed = object.__new__(ExecutionPublicationProvenance)
    with pytest.raises(ValueError, match="provenance is malformed"):
        ExecutionPublicationProvenance.admit(malformed)


def test_persistence_constructors_reject_untyped_nested_records() -> None:
    key = GraphCommitKey(GraphRunId("run"), 0)
    with pytest.raises(Graph.SnapshotMismatchError, match="typed immutable records"):
        GraphPersistenceWriteSet(key, cast(tuple[PersistedGraphInput[str], ...], (None,)), ())
    with pytest.raises(Graph.SnapshotMismatchError, match="complete write set"):
        GraphPersistenceCommit(
            (),
            None,
            running_state(),
            cast(GraphPersistenceWriteSet[str], None),
        )


def test_session_receipt_admission_owns_scope_commit_and_evidence() -> None:
    key = GraphCommitKey(GraphRunId("run"), 0)
    session = EncodedAgentSession("test.session", 1, b"payload")
    receipt = GraphSessionReceipt.for_commit((), key, session)
    assert receipt.admit() == receipt
    assert receipt.evidence != GraphSessionReceipt.for_commit((GraphNodeId("child"),), key, session).evidence
    assert (
        receipt.evidence != GraphSessionReceipt.for_commit((), GraphCommitKey(GraphRunId("run"), 1), session).evidence
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="typed immutable tuple"):
        GraphSessionReceipt(cast(tuple[GraphNodeId, ...], []), key, receipt.evidence)
    with pytest.raises(Graph.SnapshotMismatchError, match="malformed"):
        GraphSessionReceipt((), key, cast(GraphEvidenceCommitment, object()))
    with pytest.raises(Graph.SnapshotMismatchError, match="valid encoded Session"):
        GraphSessionReceipt.for_commit((), key, cast(EncodedAgentSession, object()))
    unsupported = cast(GraphSessionReceipt, object.__new__(UnsupportedSessionReceipt))
    with pytest.raises(Graph.SnapshotMismatchError, match="exact typed record"):
        unsupported.admit()


@pytest.mark.asyncio
async def test_session_receipt_is_an_atomic_part_of_commit_and_checkpoint_admission() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    request = store.requests[0]
    session = EncodedAgentSession("test.session", 1, b"payload")
    receipt = GraphSessionReceipt.for_commit(request.scope, request.writes.commit_key, session)
    bound = replace(request, agent_session=session, session_receipt=receipt)
    assert bound.admit() == bound

    with pytest.raises(Graph.SnapshotMismatchError, match="Session receipt is malformed"):
        replace(bound, session_receipt=cast(GraphSessionReceipt, object()))
    with pytest.raises(Graph.SnapshotMismatchError, match="present together"):
        replace(bound, session_receipt=None)
    with pytest.raises(Graph.SnapshotMismatchError, match="present together"):
        replace(bound, agent_session=None)
    with pytest.raises(Graph.SnapshotMismatchError, match="exact commit"):
        replace(
            bound,
            session_receipt=GraphSessionReceipt.for_commit(
                request.scope,
                GraphCommitKey(request.writes.commit_key.run_id, 1),
                session,
            ),
        )

    checkpoint = GraphCheckpoint[str](running_state(), (), (), ())
    key = GraphCommitKey(GraphRunId("run"), 0)
    checkpoint = replace(
        checkpoint, agent_session=session, session_receipt=GraphSessionReceipt.for_commit((), key, session)
    )
    assert checkpoint.admit() == checkpoint
    with pytest.raises(Graph.SnapshotMismatchError, match="Session receipt is malformed"):
        replace(checkpoint, session_receipt=cast(GraphSessionReceipt, object()))
    with pytest.raises(Graph.SnapshotMismatchError, match="present together"):
        replace(checkpoint, session_receipt=None)
    with pytest.raises(Graph.SnapshotMismatchError, match="present together"):
        replace(checkpoint, agent_session=None)
    with pytest.raises(Graph.SnapshotMismatchError, match="does not match its AgentSession"):
        replace(
            checkpoint,
            session_receipt=GraphSessionReceipt.for_commit(
                (),
                key,
                EncodedAgentSession("test.session", 1, b"other"),
            ),
        )
    relabeled = GraphSessionReceipt(
        (),
        key,
        GraphSessionReceipt.for_commit((GraphNodeId("child"),), key, session).evidence,
    )
    with pytest.raises(Graph.SnapshotMismatchError, match="does not match its AgentSession"):
        replace(checkpoint, session_receipt=relabeled)


@pytest.mark.asyncio
async def test_persisted_input_readmission_normalizes_malformed_nested_confirmation_fields() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    record = deepcopy(store.checkpoint().graph_inputs[0])
    malformed_key = object.__new__(GraphCommitKey)
    object.__setattr__(record, "birth", malformed_key)
    with pytest.raises(Graph.SnapshotMismatchError, match="commit key is malformed"):
        record.admit()

    record = deepcopy(store.checkpoint().graph_inputs[0])
    malformed_evidence = object.__new__(GraphEvidenceCommitment)
    object.__setattr__(record, "evidence", malformed_evidence)
    with pytest.raises(Graph.SnapshotMismatchError, match="commitment is malformed"):
        record.admit()


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["children", "inputs", "publications"])
async def test_checkpoint_requires_canonical_order_for_every_persisted_segment(segment: str) -> None:
    store = MemoryPersistence[str]()
    await nested_graph([], depth=2).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    if segment == "children":
        changed = {"child_runs": tuple(reversed(checkpoint.child_runs))}
    elif segment == "inputs":
        changed = {"graph_inputs": tuple(reversed(checkpoint.graph_inputs))}
    else:
        changed = {"publications": tuple(reversed(checkpoint.publications))}

    with pytest.raises(Graph.SnapshotMismatchError, match="canonical"):
        replace(checkpoint, **changed)


@pytest.mark.asyncio
async def test_checkpoint_rejects_an_adjacent_duplicate_publication_coordinate() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    publications = tuple(
        sorted((*checkpoint.publications, checkpoint.publications[0]), key=lambda item: item.coordinate)
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="repeats one publication coordinate"):
        replace(checkpoint, publications=publications)


def test_checkpoint_config_cursor_projection_readmits_authoritative_states() -> None:
    checkpoint = GraphCheckpoint[str](running_state(), (), (), ())
    object.__setattr__(checkpoint.root_state, "revision", -1)

    with pytest.raises(Graph.SnapshotMismatchError, match="malformed authoritative state"):
        _ = checkpoint.config_cursors


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["values", "state", "continuation", "identity", "config", "commit", "type"])
async def test_recovery_cannot_be_combined_with_other_state_or_initial_input(conflict: str) -> None:
    store = MemoryPersistence[str]()
    graph = linear_graph([])
    completed = await graph.run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    recovery = GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
    run = cast(RecoveryRun, graph.run)
    with pytest.raises(Graph.SnapshotMismatchError, match="durable recovery"):
        if conflict == "values":
            await run(Graph.values(value="replacement"), recovery=recovery)
        elif conflict == "state":
            await run(recovery=recovery, state=completed.state)
        elif conflict == "continuation":
            await run(recovery=recovery, continuation=completed.continuation)
        elif conflict == "identity":
            await run(recovery=recovery, run_id="other")
        elif conflict == "config":
            await run(recovery=recovery, activation_config=activation_config())
        elif conflict == "commit":
            await run(recovery=recovery, commit=DurableGraphCommit(STRING_CODEC, MemoryPersistence[str]()))
        else:
            await run(recovery=cast(GraphRecovery[str], object()))

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.persistence_fixtures import (
    STRING_CODEC,
    MemoryPersistence,
    capture_graph_input,
    decode_strings,
    encode_strings,
    linear_graph,
)

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, FrameKind
from mote_kernel.execution.persistence import (
    DurableGraphCommit,
    EncodedFrame,
    GraphPersistenceCommit,
    GraphPersistenceWriteSet,
    PersistedPublication,
)
from mote_kernel.execution.run_context import ConfirmedPublication, ExecutionPublicationProvenance, ScopedFrameIndex
from mote_kernel.state.graph_state import (
    GraphExecutionAttemptId,
    GraphExecutionLease,
    GraphExecutionToken,
    GraphRunState,
)


def changed_input(request: GraphPersistenceCommit[str]) -> GraphPersistenceCommit[str]:
    original = request.writes.graph_inputs[0]
    payload = b'{"value":"different"}'
    frame = EncodedFrame(original.frame.codec_id, original.frame.codec_version, payload, original.frame.config_cursor)
    changed = capture_graph_input(original.coordinate, frame, original.birth)
    writes = replace(request.writes, graph_inputs=(changed,))
    candidate = replace(request.candidate_state, graph_input_evidence=changed.evidence)
    return replace(request, candidate_state=candidate, writes=writes)


COMMIT_CHANGES: tuple[Callable[[GraphPersistenceCommit[str]], GraphPersistenceCommit[str]], ...] = (
    changed_input,
    lambda request: replace(request, scope=("different",)),
    lambda request: replace(request, expected_revision=0),
    lambda request: replace(request, candidate_state=replace(request.candidate_state, revision=1)),
    lambda request: replace(request, writes=replace(request.writes, graph_inputs=())),
    lambda request: replace(
        request, writes=replace(request.writes, commit_key=replace(request.writes.commit_key, revision=1))
    ),
    lambda request: cast(GraphPersistenceCommit[str], request.candidate_state),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    COMMIT_CHANGES,
    ids=["payload", "scope", "cas", "state", "missing-input", "commit-key", "state-only-ack"],
)
async def test_exact_confirmation_covers_every_commit_fact(
    change: Callable[[GraphPersistenceCommit[str]], GraphPersistenceCommit[str]],
) -> None:
    calls: list[str] = []

    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        return change(request)

    with pytest.raises(Graph.SnapshotMismatchError):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))
    assert calls == []


@pytest.mark.asyncio
async def test_commit_receipt_replay_is_exact_and_same_key_other_values_conflict() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, store))
    request = store.requests[0]
    confirmed = await store(request)
    assert confirmed == request
    assert confirmed is not request
    with pytest.raises(ValueError, match="different content"):
        await store(changed_input(request))


@pytest.mark.parametrize("revision", [-1, True])
def test_persistent_cas_precondition_has_exact_integer_semantics(revision: int) -> None:
    from_state = cast(GraphRunState, None)
    with pytest.raises(Graph.SnapshotMismatchError, match="expected revision"):
        GraphPersistenceCommit((), revision, from_state, cast(GraphPersistenceWriteSet[str], None))


@pytest.mark.parametrize(
    "codec",
    [
        FrameCodec(" ", 1, encode_strings, decode_strings),
        FrameCodec("strings", 0, encode_strings, decode_strings),
        FrameCodec("strings", True, encode_strings, decode_strings),
        FrameCodec("strings", 1, cast(Callable[[Graph.Values[str]], bytes], None), decode_strings),
        FrameCodec("strings", 1, encode_strings, cast(Callable[[bytes], Graph.Values[str]], None)),
    ],
)
def test_missing_or_invalid_codec_fails_assembly(codec: FrameCodec[str]) -> None:
    with pytest.raises(Graph.ValidationError):
        DurableGraphCommit(codec, MemoryPersistence[str]())


@pytest.mark.parametrize("missing", ["codec", "writer"])
def test_durable_commit_requires_both_capabilities(missing: str) -> None:
    with pytest.raises(Graph.ValidationError, match="requires"):
        DurableGraphCommit(
            cast(FrameCodec[str], None) if missing == "codec" else STRING_CODEC,
            cast(MemoryPersistence[str], None) if missing == "writer" else MemoryPersistence[str](),
        )


def rejected_encoder(_values: Graph.Values[str]) -> bytes:
    raise ValueError("encoder rejected")


def rejected_decoder(_payload: bytes) -> Graph.Values[str]:
    raise ValueError("decoder rejected")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "codec",
    [
        FrameCodec("strings", 1, rejected_encoder, decode_strings),
        FrameCodec("strings", 1, encode_strings, rejected_decoder),
        FrameCodec("strings", 1, lambda _values: cast(bytes, "not-bytes"), decode_strings),
        FrameCodec("strings", 1, encode_strings, lambda _payload: cast(Graph.Values[str], b"not-values")),
        FrameCodec("strings", 1, encode_strings, lambda _payload: Graph.values(other="input")),
        FrameCodec("strings", 1, encode_strings, lambda _payload: cast(Graph.Values[str], Graph.values(value=1))),
        FrameCodec("strings", 1, encode_strings, lambda _payload: Graph.values(value="changed")),
    ],
    ids=["encoder-error", "decoder-error", "not-bytes", "not-values", "wrong-names", "wrong-type", "lossy"],
)
async def test_codec_failure_is_before_any_durable_write(codec: FrameCodec[str]) -> None:
    store = MemoryPersistence[str]()
    calls: list[str] = []
    with pytest.raises(Graph.ValueAdmissionError):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(codec, store))
    assert store.requests == []
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stable_calls", [1, 2])
async def test_nondeterministic_encoder_cannot_commit(stable_calls: int) -> None:
    encodings: list[bytes] = []

    def encode(values: Graph.Values[str]) -> bytes:
        payload = encode_strings(values) + b" " * max(0, len(encodings) - stable_calls + 1)
        encodings.append(payload)
        return payload

    store = MemoryPersistence[str]()
    codec = FrameCodec("nondeterministic", 1, encode, decode_strings)
    with pytest.raises(Graph.ValueAdmissionError, match="deterministic"):
        await linear_graph([]).run(Graph.values(value="input"), commit=DurableGraphCommit(codec, store))
    assert store.requests == []


@pytest.mark.parametrize(
    "values",
    [
        {"codec_id": "bad\nidentity"},
        {"codec_version": True},
        {"codec_version": 0},
        {"payload": bytearray(b"{}")},
    ],
)
def test_encoded_evidence_rejects_noncanonical_or_corrupt_fields(values: dict[str, object]) -> None:
    frame = EncodedFrame("strings", 1, b"{}")
    with pytest.raises(Graph.SnapshotMismatchError):
        replace(frame, **values)


def test_codec_rejects_non_bytes_decode_input() -> None:
    with pytest.raises(Graph.ValueAdmissionError, match="requires bytes"):
        STRING_CODEC.decode(cast(bytes, bytearray()))


@pytest.mark.parametrize(
    "changes",
    [
        {"definition_id": ""},
        {"definition_version": True},
        {"definition_version": 0},
        {"frame_kind": True},
        {"owner_ordinal": True},
        {"owner_ordinal": -1},
    ],
)
def test_descriptor_coordinates_cannot_use_bool_integer_equality(changes: dict[str, object]) -> None:
    descriptor = FrameDescriptorIdentity("graph", 1, FrameKind.GRAPH_INPUT, 0)
    with pytest.raises(Graph.ValidationError, match="descriptor identity"):
        replace(descriptor, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"revision": -1},
        {"revision": 0.0},
        {"superstep": False},
        {"definition_version": True},
        {"execution_sequence": False},
        {"execution": "not-a-lease"},
        {"execution": GraphExecutionLease(cast(GraphExecutionToken, None))},
    ],
)
async def test_invalid_confirmed_state_is_admitted_by_the_state_owner(changes: dict[str, object]) -> None:
    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        return replace(request, candidate_state=replace(request.candidate_state, **changes))

    with pytest.raises(Graph.SnapshotMismatchError, match="candidate is malformed"):
        await linear_graph([]).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))


@pytest.mark.asyncio
async def test_confirmation_rejects_an_untyped_snapshot_at_the_state_owner() -> None:
    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        return replace(request, candidate_state=cast(GraphRunState, None))

    with pytest.raises(Graph.SnapshotMismatchError, match="candidate is malformed"):
        await linear_graph([]).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))


PUBLICATION_RECEIPT_CHANGES: tuple[Callable[[PersistedPublication[str]], PersistedPublication[str]], ...] = (
    lambda item: replace(
        item,
        frame=EncodedFrame(
            item.frame.codec_id, item.frame.codec_version, b'{"value":"other"}', item.frame.config_cursor
        ),
    ),
    lambda item: replace(item, birth=replace(item.birth, revision=item.birth.revision - 1)),
    lambda item: replace(item, birth=replace(item.birth, revision=cast(int, float(item.birth.revision)))),
    lambda item: replace(
        item,
        provenance=ExecutionPublicationProvenance(
            replace(item.provenance.execution_token, attempt_id=GraphExecutionAttemptId("other"))
        ),
    ),
    lambda item: replace(
        item,
        provenance=ExecutionPublicationProvenance(replace(item.provenance.execution_token, generation=True)),
    ),
    lambda item: replace(
        item, coordinate=replace(item.coordinate, descriptor=replace(item.coordinate.descriptor, owner_ordinal=99))
    ),
    lambda item: replace(item, frame=replace(item.frame, codec_id="other")),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    PUBLICATION_RECEIPT_CHANGES,
    ids=["bytes", "birth-revision", "float-birth-revision", "attempt", "bool-generation", "descriptor", "codec"],
)
async def test_nonexact_publication_confirmation_never_exposes_values_to_a_successor(
    change: Callable[[PersistedPublication[str]], PersistedPublication[str]],
) -> None:
    store = MemoryPersistence[str]()
    calls: list[str] = []

    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        if request.writes.publications:
            changed = change(request.writes.publications[0])
            return replace(request, writes=replace(request.writes, publications=(changed,)))
        return await store(request)

    with pytest.raises(Graph.SnapshotMismatchError):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))
    assert calls == ["first"]
    assert not any(request.writes.publications for request in store.requests)


@pytest.mark.asyncio
async def test_publication_preparation_failure_happens_before_the_durable_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_publication(
        _frames: ScopedFrameIndex[str],
        _record: ConfirmedPublication[str],
    ) -> ScopedFrameIndex[str]:
        raise RuntimeError("publication preparation failed")

    monkeypatch.setattr(ScopedFrameIndex, "add_publication", reject_publication)
    store = MemoryPersistence[str]()
    calls: list[str] = []
    with pytest.raises(RuntimeError, match="publication preparation failed"):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, store))
    assert calls == ["first"]
    assert not any(request.writes.publications for request in store.requests)

from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.persistence_fixtures import (
    STRING_CODEC,
    MemoryPersistence,
    capture_graph_input,
    capture_publication,
    decode_strings,
    encode_strings,
    linear_graph,
    nested_graph,
)

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.persistence import (
    DurableGraphCommit,
    EncodedFrame,
    GraphPersistenceCommit,
    GraphRecovery,
)
from mote_kernel.execution.run_context import ExecutionPublicationProvenance
from mote_kernel.state.graph_state import (
    GraphEvidenceCommitment,
    GraphExecutionAttemptId,
    GraphNodeId,
)


class PermissiveScope(tuple[str, ...]):
    def __eq__(self, other: object) -> bool:
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
@pytest.mark.parametrize("version_conflict", [False, True], ids=["identity", "version"])
async def test_valid_evidence_commitment_cannot_bypass_the_bound_codec(
    segment: str,
    version_conflict: bool,
) -> None:
    calls: list[str] = []
    decoded: list[bytes] = []

    def decode(payload: bytes) -> Graph.Values[str]:
        decoded.append(payload)
        return decode_strings(payload)

    store = MemoryPersistence[str]()
    await linear_graph(calls).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    record = checkpoint.graph_inputs[0] if segment == "input" else checkpoint.publications[0]
    foreign = EncodedFrame(
        record.frame.codec_id if version_conflict else "another-codec",
        record.frame.codec_version + 1 if version_conflict else record.frame.codec_version,
        record.frame.payload,
        record.frame.config_cursor,
    )
    if segment == "input":
        graph_input = checkpoint.graph_inputs[0]
        changed = capture_graph_input(graph_input.coordinate, foreign, graph_input.birth)
        checkpoint = replace(
            checkpoint,
            root_state=replace(checkpoint.root_state, graph_input_evidence=changed.evidence),
            graph_inputs=(changed,),
        )
    else:
        publication = checkpoint.publications[0]
        changed = capture_publication(
            publication.coordinate,
            foreign,
            publication.birth,
            publication.provenance,
        )
        activation = publication.coordinate.activation
        settlements = tuple(
            replace(settlement, evidence=changed.evidence)
            if (
                settlement.reference.activation.run_id == activation.scope_run.graph_run_id
                and settlement.reference.activation.superstep == activation.superstep
                and settlement.reference.activation.node_id == activation.node_id
            )
            else settlement
            for settlement in checkpoint.root_state.settled_publications
        )
        checkpoint = replace(
            checkpoint,
            root_state=replace(checkpoint.root_state, settled_publications=settlements),
            publications=(changed, *checkpoint.publications[1:]),
        )
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError, match="codec identity or version"):
        await linear_graph(calls).run(
            recovery=GraphRecovery(checkpoint, DurableGraphCommit(replace(STRING_CODEC, decoder=decode), store))
        )
    assert decoded == ([] if segment == "input" else [checkpoint.graph_inputs[0].frame.payload])
    assert len(store.requests) == committed
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payload", b'{"value":"tampered"}'),
        ("payload", bytearray(b"{}")),
        ("codec_id", " "),
        ("codec_id", "another-codec"),
        ("codec_version", True),
        ("codec_version", 2),
        ("config_cursor", object()),
    ],
    ids=[
        "stale-commitment",
        "mutable-payload",
        "codec-id",
        "changed-codec-id",
        "codec-version",
        "changed-codec-version",
        "config-type",
    ],
)
async def test_recovery_read_readmits_delivered_frames_before_decode(segment: str, field: str, value: object) -> None:
    decoded: list[bytes] = []

    def decode(payload: bytes) -> Graph.Values[str]:
        decoded.append(payload)
        return decode_strings(payload)

    codec = FrameCodec(STRING_CODEC.codec_id, STRING_CODEC.version, encode_strings, decode)
    calls: list[str] = []
    store = MemoryPersistence[str]()
    await linear_graph(calls).run(Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(codec, store))
    recovery = GraphRecovery(deepcopy(store.checkpoint()), DurableGraphCommit(codec, store))
    frame = (
        recovery.checkpoint.graph_inputs[0].frame if segment == "input" else recovery.checkpoint.publications[0].frame
    )
    object.__setattr__(frame, field, value)
    decoded.clear()
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError):
        await linear_graph(calls).run(recovery=recovery)
    assert decoded == []
    assert len(store.requests) == committed
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
@pytest.mark.parametrize("component", ["record", "coordinate", "scope", "descriptor", "confirmation"])
async def test_recovery_read_readmits_typed_coordinates_and_confirmation(segment: str, component: str) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    await linear_graph(calls).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    recovery = GraphRecovery(deepcopy(store.checkpoint()), DurableGraphCommit(STRING_CODEC, store))
    checkpoint = recovery.checkpoint
    if segment == "input":
        graph_input = checkpoint.graph_inputs[0]
        if component == "record":
            object.__setattr__(checkpoint, "graph_inputs", (None,))
        elif component == "coordinate":
            object.__setattr__(graph_input, "coordinate", None)
        elif component == "scope":
            object.__setattr__(graph_input.coordinate.scope_run, "scope", (" ",))
        elif component == "descriptor":
            object.__setattr__(graph_input.coordinate.descriptor, "owner_ordinal", True)
        else:
            object.__setattr__(graph_input, "frame", None)
    else:
        publication = checkpoint.publications[0]
        if component == "record":
            object.__setattr__(checkpoint, "publications", (None,))
        elif component == "coordinate":
            object.__setattr__(publication.coordinate.activation, "superstep", True)
        elif component == "scope":
            object.__setattr__(publication.coordinate.activation.scope_run, "graph_run_id", " ")
        elif component == "descriptor":
            object.__setattr__(publication.coordinate.descriptor, "owner_ordinal", True)
        else:
            object.__setattr__(publication, "birth", None)
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError):
        await linear_graph(calls).run(recovery=recovery)
    assert len(store.requests) == committed
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["frame", "write-set", "commit-key", "graph-inputs", "publications"])
async def test_commit_receipt_is_readmitted_before_confirmation(component: str) -> None:
    calls: list[str] = []

    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        receipt = deepcopy(request)
        if component == "frame":
            object.__setattr__(receipt.writes.graph_inputs[0].frame, "payload", b'{"value":"tampered"}')
        elif component == "write-set":
            object.__setattr__(receipt, "writes", None)
        elif component == "commit-key":
            object.__setattr__(receipt.writes, "commit_key", None)
        elif component == "graph-inputs":
            object.__setattr__(receipt.writes, "graph_inputs", (None,))
        else:
            object.__setattr__(receipt.writes, "publications", (None,))
        return receipt

    with pytest.raises(Graph.SnapshotMismatchError):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("component", ["scope", "candidate", "commit-key", "frame"])
async def test_writer_cannot_confirm_an_in_place_mutation_of_the_original_request(component: str) -> None:
    calls: list[str] = []

    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        if component == "scope":
            object.__setattr__(request, "scope", (GraphNodeId("other"),))
        elif component == "candidate":
            object.__setattr__(request.candidate_state, "revision", 99)
        elif component == "commit-key":
            object.__setattr__(request.writes.commit_key, "revision", 99)
        else:
            object.__setattr__(request.writes.graph_inputs[0].frame, "payload", b'{"value":"changed"}')
        return request

    with pytest.raises(Graph.SnapshotMismatchError):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))
    assert calls == []


@pytest.mark.asyncio
async def test_writer_cannot_coherently_mutate_the_original_request() -> None:
    calls: list[str] = []

    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        object.__setattr__(
            request,
            "candidate_state",
            replace(request.candidate_state, config_digest="writer-forged-config"),
        )
        assert request.admit() == request
        return request

    with pytest.raises(Graph.SnapshotMismatchError, match="exact state and complete value write set"):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
async def test_persistence_commit_requires_every_authoritative_value_commitment(segment: str) -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    if segment == "input":
        request = store.requests[0]
        candidate = replace(request.candidate_state, graph_input_evidence=None)
    else:
        request = next(item for item in store.requests if item.writes.publications)
        publication = request.writes.publications[0]
        activation = publication.coordinate.activation
        settlements = tuple(
            replace(item, evidence=None)
            if (
                item.reference.activation.superstep == activation.superstep
                and item.reference.activation.node_id == activation.node_id
            )
            else item
            for item in request.candidate_state.settled_publications
        )
        candidate = replace(request.candidate_state, settled_publications=settlements)

    with pytest.raises(Graph.SnapshotMismatchError, match="missing value evidence commitments"):
        replace(request, candidate_state=candidate)


@pytest.mark.asyncio
async def test_persistence_commit_rejects_a_publication_from_another_scope() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    request = next(item for item in store.requests if item.writes.publications)

    with pytest.raises(Graph.SnapshotMismatchError, match="different scoped run"):
        replace(request, scope=(GraphNodeId("foreign"),))


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["missing", "execution"])
async def test_persistence_commit_binds_publication_to_its_exact_settlement(mismatch: str) -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    request = next(item for item in store.requests if item.writes.publications)
    publication = request.writes.publications[0]
    if mismatch == "missing":
        coordinate = replace(
            publication.coordinate,
            activation=replace(publication.coordinate.activation, node_id=GraphNodeId("unknown")),
        )
        changed = capture_publication(
            coordinate,
            publication.frame,
            publication.birth,
            publication.provenance,
        )
    else:
        provenance = ExecutionPublicationProvenance(
            replace(
                publication.provenance.execution_token,
                attempt_id=GraphExecutionAttemptId("foreign-attempt"),
            )
        )
        changed = capture_publication(
            publication.coordinate,
            publication.frame,
            publication.birth,
            provenance,
        )
    writes = replace(request.writes, publications=(changed,))

    with pytest.raises(Graph.SnapshotMismatchError, match="authoritative settlement"):
        replace(request, writes=writes)


@pytest.mark.asyncio
async def test_recovery_converts_a_canonical_wrong_business_type_to_snapshot_mismatch() -> None:
    class WrongString(str):
        pass

    def decode(payload: bytes) -> Graph.Values[str]:
        values = decode_strings(payload)
        return cast(Graph.Values[str], Graph.values(value=WrongString(values["value"])))

    codec = FrameCodec(STRING_CODEC.codec_id, STRING_CODEC.version, encode_strings, decode)
    store = MemoryPersistence[str]()
    calls: list[str] = []
    await linear_graph(calls).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="cannot be admitted by the compiled graph"):
        await linear_graph(calls).run(recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(codec, store)))
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["record", "state"])
@pytest.mark.parametrize("fact", ["revision", "provenance"])
async def test_publication_birth_facts_must_match_the_authoritative_settlement(
    owner: str,
    fact: str,
) -> None:
    calls: list[str] = []
    decoded: list[bytes] = []

    def decode(payload: bytes) -> Graph.Values[str]:
        decoded.append(payload)
        return decode_strings(payload)

    store = MemoryPersistence[str]()
    await linear_graph(calls).run(
        Graph.values(value="input"),
        run_id="run",
        commit=DurableGraphCommit(STRING_CODEC, store),
    )
    checkpoint = store.checkpoint()
    publication = next(
        item for item in checkpoint.publications if item.coordinate.activation.node_id == GraphNodeId("first")
    )
    forged_token = replace(
        publication.provenance.execution_token,
        attempt_id=GraphExecutionAttemptId("forged-attempt"),
    )
    if owner == "record":
        birth = (
            replace(publication.birth, revision=publication.birth.revision - 1)
            if fact == "revision"
            else publication.birth
        )
        provenance = publication.provenance if fact == "revision" else ExecutionPublicationProvenance(forged_token)
        changed = capture_publication(publication.coordinate, publication.frame, birth, provenance)
        checkpoint = replace(
            checkpoint,
            publications=tuple(changed if item is publication else item for item in checkpoint.publications),
        )
    else:
        activation = publication.coordinate.activation
        settlements = tuple(
            replace(
                settlement,
                commit_revision=publication.birth.revision - 1 if fact == "revision" else settlement.commit_revision,
                execution=forged_token if fact == "provenance" else settlement.execution,
            )
            if (
                settlement.reference.activation.superstep == activation.superstep
                and settlement.reference.activation.node_id == activation.node_id
            )
            else settlement
            for settlement in checkpoint.root_state.settled_publications
        )
        checkpoint = replace(
            checkpoint,
            root_state=replace(checkpoint.root_state, settled_publications=settlements),
        )
    committed = len(store.requests)

    with pytest.raises(Graph.SnapshotMismatchError, match="authoritative settlement"):
        await linear_graph(calls).run(
            recovery=GraphRecovery(
                checkpoint,
                DurableGraphCommit(replace(STRING_CODEC, decoder=decode), store),
            )
        )
    assert decoded == []
    assert len(store.requests) == committed
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_sibling_payload_swap_with_recaptured_records_cannot_change_value_ownership() -> None:
    calls: list[str] = []
    decoded: list[bytes] = []

    def decode(payload: bytes) -> Graph.Values[str]:
        decoded.append(payload)
        return decode_strings(payload)

    store = MemoryPersistence[str]()
    await linear_graph(calls).run(
        Graph.values(value="input"),
        run_id="run",
        commit=DurableGraphCommit(STRING_CODEC, store),
    )
    checkpoint = store.checkpoint()
    first, second = checkpoint.publications
    swapped = tuple(
        sorted(
            (
                capture_publication(first.coordinate, second.frame, first.birth, first.provenance),
                capture_publication(second.coordinate, first.frame, second.birth, second.provenance),
            ),
            key=lambda item: item.coordinate,
        )
    )

    with pytest.raises(Graph.SnapshotMismatchError, match="authoritative settlement"):
        await linear_graph(calls).run(
            recovery=GraphRecovery(
                replace(checkpoint, publications=swapped),
                DurableGraphCommit(replace(STRING_CODEC, decoder=decode), store),
            )
        )
    assert decoded == []
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
@pytest.mark.parametrize("mutation", ["missing", "foreign"])
async def test_durable_state_requires_its_exact_value_commitments(segment: str, mutation: str) -> None:
    calls: list[str] = []
    decoded: list[bytes] = []

    def decode(payload: bytes) -> Graph.Values[str]:
        decoded.append(payload)
        return decode_strings(payload)

    store = MemoryPersistence[str]()
    await linear_graph(calls).run(
        Graph.values(value="input"),
        run_id="run",
        commit=DurableGraphCommit(STRING_CODEC, store),
    )
    checkpoint = store.checkpoint()
    evidence = None if mutation == "missing" else GraphEvidenceCommitment(b"x" * 32)
    if segment == "input":
        state = replace(checkpoint.root_state, graph_input_evidence=evidence)
    else:
        first = checkpoint.root_state.settled_publications[0]
        state = replace(
            checkpoint.root_state,
            settled_publications=(replace(first, evidence=evidence), *checkpoint.root_state.settled_publications[1:]),
        )
    committed = len(store.requests)

    with pytest.raises(Graph.SnapshotMismatchError, match=r"commitment|authoritative"):
        await linear_graph(calls).run(
            recovery=GraphRecovery(
                replace(checkpoint, root_state=state),
                DurableGraphCommit(replace(STRING_CODEC, decoder=decode), store),
            )
        )
    assert decoded == []
    assert len(store.requests) == committed
    assert calls == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [[], "", (" ",), PermissiveScope()])
async def test_commit_scope_is_canonical_before_receipt_equality(scope: object) -> None:
    calls: list[str] = []

    async def writer(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        receipt = deepcopy(request)
        object.__setattr__(receipt, "scope", scope)
        return receipt

    with pytest.raises(Graph.SnapshotMismatchError, match="scope-run coordinate"):
        await linear_graph(calls).run(Graph.values(value="input"), commit=DurableGraphCommit(STRING_CODEC, writer))
    assert calls == []


@pytest.mark.asyncio
async def test_recovery_read_readmits_its_bound_commit_capability() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    recovery = GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store))
    object.__setattr__(recovery, "commit", cast(DurableGraphCommit[str], None))
    with pytest.raises(Graph.ValidationError, match="durable commit capability"):
        await linear_graph([]).run(recovery=recovery)


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [0, 2], ids=["root", "nested"])
@pytest.mark.parametrize("change", ["missing", "invented"])
async def test_completed_publications_exactly_match_the_full_settlement_ledger(depth: int, change: str) -> None:
    calls: list[str] = []
    store = MemoryPersistence[str]()
    await nested_graph(calls, depth=depth).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    first = next(item for item in checkpoint.publications if item.coordinate.activation.node_id == GraphNodeId("first"))
    if change == "missing":
        publications = tuple(item for item in checkpoint.publications if item.coordinate != first.coordinate)
    else:
        invented = capture_publication(
            replace(first.coordinate, activation=replace(first.coordinate.activation, superstep=1)),
            first.frame,
            first.birth,
            first.provenance,
        )
        publications = tuple(sorted((*checkpoint.publications, invented), key=lambda item: item.coordinate))
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError, match="settlement"):
        await nested_graph(calls, depth=depth).run(
            recovery=GraphRecovery(
                replace(checkpoint, publications=publications), DurableGraphCommit(STRING_CODEC, store)
            )
        )
    assert len(store.requests) == committed
    assert calls == ["first", "second"]

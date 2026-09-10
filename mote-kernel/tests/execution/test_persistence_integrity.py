from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.persistence_fixtures import (
    STRING_CODEC,
    MemoryPersistence,
    decode_strings,
    encode_strings,
    linear_graph,
    nested_graph,
)

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.persistence import DurableGraphCommit, EncodedFrame, GraphPersistenceCommit, GraphRecovery
from mote_kernel.state.graph_state import GraphNodeId


class PermissiveScope(tuple[str, ...]):
    def __eq__(self, other: object) -> bool:
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
@pytest.mark.parametrize("version_conflict", [False, True], ids=["identity", "version"])
async def test_valid_frame_digest_cannot_bypass_the_bound_codec(segment: str, version_conflict: bool) -> None:
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
    original = checkpoint.graph_inputs[0].frame if segment == "input" else checkpoint.publications[0].frame
    foreign = EncodedFrame.capture(
        original.codec_id if version_conflict else "another-codec",
        original.codec_version + 1 if version_conflict else original.codec_version,
        original.payload,
        original.config_cursor,
    )
    if segment == "input":
        checkpoint = replace(checkpoint, graph_inputs=(replace(checkpoint.graph_inputs[0], frame=foreign),))
    else:
        checkpoint = replace(
            checkpoint,
            publications=(replace(checkpoint.publications[0], frame=foreign), *checkpoint.publications[1:]),
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
        ("frame_digest", "0" * 64),
        ("codec_id", " "),
        ("codec_id", "another-codec"),
        ("codec_version", True),
        ("codec_version", 2),
        ("config_cursor", object()),
    ],
    ids=[
        "stale-digest",
        "mutable-payload",
        "wrong-digest",
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
            object.__setattr__(publication, "acknowledged_revision", True)
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
        invented = replace(
            first,
            coordinate=replace(first.coordinate, activation=replace(first.coordinate.activation, superstep=1)),
        )
        publications = tuple(sorted((*checkpoint.publications, invented), key=lambda item: item.coordinate))
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError, match="settlement ledger"):
        await nested_graph(calls, depth=depth).run(
            recovery=GraphRecovery(
                replace(checkpoint, publications=publications), DurableGraphCommit(STRING_CODEC, store)
            )
        )
    assert len(store.requests) == committed
    assert calls == ["first", "second"]

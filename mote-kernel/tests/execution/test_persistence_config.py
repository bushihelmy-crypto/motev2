from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence, encode_strings

from mote_kernel.config import Config, ConfigSnapshot, ConfigSnapshotKey
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.graph.values import _make_single_graph_value
from mote_kernel.execution.persistence import DurableGraphCommit, EncodedFrame, GraphPersistenceCommit, GraphRecovery
from mote_kernel.loop.config import ReActRuntimeConfig
from mote_kernel.state.graph_state import GraphConfigCursor, GraphDefinitionId, GraphDefinitionVersion


def config_at(
    revision: int,
    *,
    definition: str = "persistent-config",
    version: int = 1,
    payload: bytes | None = None,
) -> Config:
    key = ConfigSnapshotKey(GraphDefinitionId(definition), GraphDefinitionVersion(version), revision)
    snapshot = ConfigSnapshot.capture(key, f'{{"revision":{revision}}}'.encode() if payload is None else payload)
    projection = ReActRuntimeConfig(key, key.definition_id, key.definition_version)
    return Config(snapshot, projection, projection, projection, projection, (projection,))


def config_graph(successor: Config, seen: list[Config | None]) -> Graph[str]:
    graph = Graph[str]("config-history")

    async def observe(values: Graph.Values[str]) -> Graph.Values[str]:
        seen.append(values.activation_config)
        return _make_single_graph_value("value", values["value"], successor)

    async def consume(values: Graph.Values[str]) -> Graph.Values[str]:
        seen.append(values.activation_config)
        return values

    graph.add_node("observe", observe, inputs={"value": graph.graph_input("value", str)}, outputs={"value": str})
    graph.add_node("consume", consume, inputs={"value": graph.node_output("observe", "value")}, outputs={"value": str})
    graph.add_edge("observe", "consume")
    graph.add_edge("consume", Graph.END)
    graph.set_outputs({"value": graph.output_ref("consume", "value")})
    return graph


@pytest.mark.asyncio
async def test_historical_frames_resolve_their_own_config_without_serializing_capabilities() -> None:
    initial, successor = (config_at(1), config_at(2))
    seen: list[Config | None] = []
    store = MemoryPersistence[str]()
    store.fail_when = lambda request: request.candidate_state.superstep == 1
    with pytest.raises(OSError):
        await config_graph(successor, seen).run(
            Graph.values(value="business"),
            run_id="run",
            activation_config=initial,
            commit=DurableGraphCommit(STRING_CODEC, store),
        )
    checkpoint = store.checkpoint()
    assert checkpoint.graph_inputs[0].frame.config_cursor == initial.config_cursor
    assert checkpoint.publications[0].frame.config_cursor == successor.config_cursor
    assert checkpoint.publications[0].frame.payload == b'{"value":"business"}'
    restored_initial, restored_successor = (config_at(1), config_at(2))
    store.reopen()
    result = await config_graph(restored_successor, seen).run(
        recovery=GraphRecovery(
            checkpoint, DurableGraphCommit(STRING_CODEC, store), (restored_initial, restored_successor)
        )
    )
    assert isinstance(result, Graph.CompletedResult)
    assert seen[0] is initial
    assert seen[1] is restored_successor
    assert result.outputs.activation_config is restored_successor


@pytest.mark.asyncio
@pytest.mark.parametrize("configs", [(), (config_at(2),), (config_at(1), config_at(1)), (cast(Config, None),)])
async def test_config_admission_never_substitutes_latest_or_accepts_ambiguity(configs: tuple[Config, ...]) -> None:
    store = MemoryPersistence[str]()
    await config_graph(config_at(2), []).run(
        Graph.values(value="business"),
        run_id="run",
        activation_config=config_at(1),
        commit=DurableGraphCommit(STRING_CODEC, store),
    )
    store.unavailable = True
    with pytest.raises(Graph.SnapshotMismatchError, match="Config"):
        await config_graph(config_at(2), []).run(
            recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store), configs)
        )


@pytest.mark.asyncio
async def test_business_decoder_cannot_supply_config_capabilities() -> None:
    codec = FrameCodec(
        "invalid-config-codec",
        1,
        encode_strings,
        lambda _payload: _make_single_graph_value("value", "business", config_at(1)),
    )
    store = MemoryPersistence[str]()
    with pytest.raises(Graph.ValueAdmissionError, match="without encoding Config"):
        await config_graph(config_at(2), []).run(
            Graph.values(value="business"), commit=DurableGraphCommit(codec, store)
        )
    assert store.requests == []


@pytest.mark.parametrize(
    "cursor",
    [cast(GraphConfigCursor, object()), GraphConfigCursor(GraphDefinitionId("config"), GraphDefinitionVersion(1), 1)],
)
def test_persisted_config_reference_requires_a_valid_digest(cursor: GraphConfigCursor) -> None:
    frame = EncodedFrame.capture(STRING_CODEC.codec_id, 1, b"{}")
    with pytest.raises(Graph.SnapshotMismatchError, match="Config"):
        replace(frame, config_cursor=cursor)


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
@pytest.mark.parametrize(
    "foreign",
    [
        config_at(3),
        config_at(1, definition="another-config"),
        config_at(1, version=2),
        config_at(2, payload=b'{"conflicting":"content"}'),
    ],
    ids=["future", "definition", "version", "same-revision-digest"],
)
async def test_frame_config_must_belong_to_its_owning_state(segment: str, foreign: Config) -> None:
    initial, successor = config_at(1), config_at(2)
    seen: list[Config | None] = []
    store = MemoryPersistence[str]()
    await config_graph(successor, seen).run(
        Graph.values(value="business"),
        run_id="run",
        activation_config=initial,
        commit=DurableGraphCommit(STRING_CODEC, store),
    )
    checkpoint = store.checkpoint()
    if segment == "input":
        graph_input = checkpoint.graph_inputs[0]
        checkpoint = replace(
            checkpoint,
            graph_inputs=(
                replace(
                    graph_input,
                    frame=EncodedFrame.capture(
                        graph_input.frame.codec_id,
                        graph_input.frame.codec_version,
                        graph_input.frame.payload,
                        foreign.config_cursor,
                    ),
                ),
            ),
        )
    else:
        publication = checkpoint.publications[0]
        checkpoint = replace(
            checkpoint,
            publications=(
                replace(
                    publication,
                    frame=EncodedFrame.capture(
                        publication.frame.codec_id,
                        publication.frame.codec_version,
                        publication.frame.payload,
                        foreign.config_cursor,
                    ),
                ),
                *checkpoint.publications[1:],
            ),
        )
    committed = len(store.requests)
    with pytest.raises(Graph.SnapshotMismatchError, match="Config"):
        await config_graph(successor, seen).run(
            recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store), (initial, successor, foreign))
        )
    assert len(store.requests) == committed
    assert seen == [initial, successor]


@pytest.mark.asyncio
async def test_one_historical_config_revision_cannot_resolve_to_conflicting_snapshots() -> None:
    initial, successor = config_at(1), config_at(2)
    store = MemoryPersistence[str]()
    await config_graph(successor, []).run(
        Graph.values(value="business"),
        run_id="run",
        activation_config=initial,
        commit=DurableGraphCommit(STRING_CODEC, store),
    )
    conflicting = config_at(1, payload=b'{"different":"history"}')
    with pytest.raises(Graph.SnapshotMismatchError, match="repeats one immutable Config revision"):
        await config_graph(successor, []).run(
            recovery=GraphRecovery(
                store.checkpoint(), DurableGraphCommit(STRING_CODEC, store), (initial, conflicting, successor)
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_state", [False, True], ids=["frame", "state"])
async def test_config_lookup_requires_digest_not_only_snapshot_key(replace_state: bool) -> None:
    initial, successor = config_at(1), config_at(2)
    store = MemoryPersistence[str]()
    await config_graph(successor, []).run(
        Graph.values(value="business"),
        run_id="run",
        activation_config=initial,
        commit=DurableGraphCommit(STRING_CODEC, store),
    )
    configs = (
        (initial, config_at(2, payload=b'{"different":"state"}'))
        if replace_state
        else (config_at(1, payload=b'{"different":"frame"}'), successor)
    )
    with pytest.raises(Graph.SnapshotMismatchError, match="exact resolved Config"):
        await config_graph(successor, []).run(
            recovery=GraphRecovery(store.checkpoint(), DurableGraphCommit(STRING_CODEC, store), configs)
        )


def test_config_history_owner_rejects_same_revision_digest_conflict() -> None:
    with pytest.raises(ValueError, match="digest must match its authoritative revision"):
        config_at(2).config_cursor.admit_history(config_at(2, payload=b'{"conflicting":"content"}').config_cursor)


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
@pytest.mark.parametrize("completed", [False, True], ids=["pending", "completed"])
@pytest.mark.parametrize("nested", [False, True], ids=["root", "child"])
async def test_deleted_config_cursor_fails_at_read_before_nodes_or_writes(
    segment: str, completed: bool, nested: bool
) -> None:
    initial, successor = config_at(1), config_at(2)
    seen: list[Config | None] = []
    decoded: list[bytes] = []

    def decode(payload: bytes) -> Graph.Values[str]:
        decoded.append(payload)
        return STRING_CODEC.decode(payload)

    def graph() -> Graph[str]:
        child = config_graph(successor, seen)
        if not nested:
            return child
        parent = Graph[str]("nested-config-history")
        parent.add_node("child", child, inputs={"value": parent.graph_input("value", str)})
        parent.set_outputs({"value": parent.output_ref("child", "value")})
        return parent

    store = MemoryPersistence[str]()
    scope = ("child",) if nested else ()
    if completed:
        await graph().run(
            Graph.values(value="business"),
            run_id="run",
            activation_config=initial,
            commit=DurableGraphCommit(STRING_CODEC, store),
        )
    else:
        store.fail_when = lambda request: (
            request.scope == scope
            and (
                request.candidate_state.execution is not None
                if segment == "input"
                else request.candidate_state.superstep == 1
            )
        )
        with pytest.raises(OSError):
            await graph().run(
                Graph.values(value="business"),
                run_id="run",
                activation_config=initial,
                commit=DurableGraphCommit(STRING_CODEC, store),
            )
    recovery = GraphRecovery(
        deepcopy(store.checkpoint()),
        DurableGraphCommit(replace(STRING_CODEC, decoder=decode), store),
        (initial, successor),
    )
    frame = (
        next(record.frame for record in recovery.checkpoint.graph_inputs if record.coordinate.scope_run.scope == scope)
        if segment == "input"
        else next(
            record.frame
            for record in recovery.checkpoint.publications
            if record.coordinate.activation.scope_run.scope == scope
        )
    )
    object.__setattr__(frame, "config_cursor", None)
    before = tuple(seen)
    committed = len(store.requests)
    store.reopen()
    with pytest.raises(Graph.SnapshotMismatchError, match="integrity"):
        await graph().run(recovery=recovery)
    assert decoded == []
    assert tuple(seen) == before
    assert len(store.requests) == committed


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", ["input", "publication"])
async def test_deleted_config_cursor_in_receipt_is_not_installed_or_silently_accepted(segment: str) -> None:
    initial, successor = config_at(1), config_at(2)
    seen: list[Config | None] = []
    store = MemoryPersistence[str]()

    async def corrupt_receipt(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        receipt = await store(request)
        if segment == "input" and receipt.writes.graph_inputs:
            object.__setattr__(receipt.writes.graph_inputs[0].frame, "config_cursor", None)
        elif segment == "publication" and receipt.writes.publications:
            object.__setattr__(receipt.writes.publications[0].frame, "config_cursor", None)
        return receipt

    with pytest.raises(Graph.SnapshotMismatchError, match="integrity"):
        await config_graph(successor, seen).run(
            Graph.values(value="business"),
            run_id="run",
            activation_config=initial,
            commit=DurableGraphCommit(STRING_CODEC, corrupt_receipt),
        )
    assert seen == ([] if segment == "input" else [initial])
    checkpoint = store.checkpoint()
    assert checkpoint.graph_inputs[0].frame.config_cursor == initial.config_cursor
    if segment == "publication":
        assert checkpoint.publications[0].frame.config_cursor == successor.config_cursor
        assert checkpoint.root_state.config_cursor == successor.config_cursor
    completed = await config_graph(successor, seen).run(
        recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store), (initial, successor))
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert completed.state == store.checkpoint().root_state
    assert seen == [initial, successor]
    assert completed.outputs.activation_config is successor


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [False, True], ids=["before-observe", "after-observe"])
async def test_historical_absent_config_is_not_filled_from_available_capabilities(observed: bool) -> None:
    successor = config_at(2, definition="config-history")
    seen: list[Config | None] = []
    store = MemoryPersistence[str]()
    store.fail_when = (
        (lambda request: request.candidate_state.superstep == 1)
        if observed
        else (lambda request: request.candidate_state.execution is not None)
    )
    with pytest.raises(OSError):
        await config_graph(successor, seen).run(
            Graph.values(value="business"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
        )
    checkpoint = store.checkpoint()
    assert checkpoint.graph_inputs[0].frame.config_cursor is None
    if observed:
        assert checkpoint.root_state.config_cursor == successor.config_cursor
    store.reopen()
    completed = await config_graph(successor, seen).run(
        recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store), (successor,))
    )
    assert isinstance(completed, Graph.CompletedResult)
    assert seen == [None, successor]
    assert completed.outputs.activation_config is successor
    assert completed.state == store.checkpoint().root_state

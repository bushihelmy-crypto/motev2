from collections.abc import Callable
from dataclasses import replace
from typing import Protocol, cast

import pytest
from tests.execution.engine.factories import activation_config, running_state
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence, linear_graph

from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.persistence import (
    DurableGraphCommit,
    EncodedFrame,
    GraphCheckpoint,
    GraphRecovery,
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
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphNodeId,
    GraphRunId,
    GraphRunState,
)

PUBLICATION_CHANGES: tuple[Callable[[PersistedPublication[str]], PersistedPublication[str]], ...] = (
    lambda item: replace(item, acknowledged_revision=0),
    lambda item: replace(item, acknowledged_revision=True),
    lambda item: replace(item, acknowledged_revision=999),
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    PUBLICATION_CHANGES,
    ids=[
        "zero-revision",
        "bool-revision",
        "future-revision",
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
    checkpoint = (
        replace(checkpoint, graph_inputs=checkpoint.graph_inputs * 2)
        if segment == "input"
        else replace(checkpoint, publications=checkpoint.publications * 2)
    )
    with pytest.raises(Graph.SnapshotMismatchError, match="persistent value evidence"):
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
async def test_persistent_decoder_requires_canonical_bytes_not_just_a_matching_digest() -> None:
    store = MemoryPersistence[str]()
    await linear_graph([]).run(
        Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, store)
    )
    checkpoint = store.checkpoint()
    original = checkpoint.graph_inputs[0]
    payload = b'{"value" : "input"}'
    changed = replace(
        original,
        frame=EncodedFrame.capture(
            original.frame.codec_id, original.frame.codec_version, payload, original.frame.config_cursor
        ),
    )
    with pytest.raises(Graph.SnapshotMismatchError, match="canonical"):
        await linear_graph([]).run(
            recovery=GraphRecovery(
                replace(checkpoint, graph_inputs=(changed,)), DurableGraphCommit(STRING_CODEC, store)
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

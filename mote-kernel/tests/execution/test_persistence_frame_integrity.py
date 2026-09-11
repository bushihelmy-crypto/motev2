import json
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256

import pytest
from tests.execution.persistence_fixtures import capture_graph_input, capture_publication

from mote_kernel.execution.commit import GraphCommitKey
from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, FrameKind
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.persistence import (
    EncodedFrame,
    PersistedGraphInput,
    PersistedPublication,
)
from mote_kernel.execution.run_context import (
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
)
from mote_kernel.state.graph_state import (
    GraphConfigCursor,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphEvidenceCommitment,
    GraphExecutionAttemptId,
    GraphExecutionToken,
    GraphNodeId,
    GraphRunId,
)

RUN_ID = GraphRunId("run")
SCOPE_RUN = ScopeRunCoordinate((), RUN_ID)
CURSOR = GraphConfigCursor(GraphDefinitionId('配置"\\definition'), GraphDefinitionVersion(3), 2, "snapshot-digest")
INPUT_DESCRIPTOR = FrameDescriptorIdentity("graph", 1, FrameKind.GRAPH_INPUT, 0)
OUTPUT_DESCRIPTOR = FrameDescriptorIdentity("graph", 1, FrameKind.NODE_OUTPUT, 1)


def canonical_commitment(domain: bytes, metadata: tuple[object, ...], payload: bytes) -> bytes:
    encoded = json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return sha256(domain + b"\x00" + encoded + b"\x00" + payload).digest()


def graph_input(*, cursor: GraphConfigCursor | None = CURSOR) -> PersistedGraphInput[str]:
    coordinate = GraphInputAvailabilityCoordinate[str](SCOPE_RUN, INPUT_DESCRIPTOR)
    birth = GraphCommitKey(RUN_ID, 0)
    return capture_graph_input(coordinate, EncodedFrame("codec", 1, b"input-payload", cursor), birth)


def publication(
    *,
    node_id: str = "node",
    payload: bytes = b"publication-payload",
    revision: int = 7,
    cursor: GraphConfigCursor | None = CURSOR,
) -> PersistedPublication[str]:
    coordinate = PublicationAvailabilityCoordinate[str](
        StableActivation(SCOPE_RUN, 3, GraphNodeId(node_id)),
        OUTPUT_DESCRIPTOR,
    )
    birth = GraphCommitKey(RUN_ID, revision)
    provenance = ExecutionPublicationProvenance(
        GraphExecutionToken(2, GraphExecutionAttemptId("attempt")),
    )
    return capture_publication(coordinate, EncodedFrame("codec", 1, payload, cursor), birth, provenance)


def test_graph_input_commitment_has_one_domain_separated_canonical_encoding() -> None:
    record = graph_input()
    expected = canonical_commitment(
        b"mote.graph-input-evidence.v1",
        (
            RUN_ID,
            0,
            (),
            RUN_ID,
            ("graph", 1, FrameKind.GRAPH_INPUT.value, 0),
            "codec",
            1,
            (CURSOR.definition_id, CURSOR.definition_version, CURSOR.revision, CURSOR.digest),
        ),
        b"input-payload",
    )

    assert record.evidence.digest == expected
    assert replace(record) == record


def test_publication_commitment_has_one_domain_separated_canonical_encoding() -> None:
    record = publication()
    expected = canonical_commitment(
        b"mote.graph-publication-evidence.v1",
        (
            RUN_ID,
            7,
            (),
            RUN_ID,
            3,
            GraphNodeId("node"),
            ("graph", 1, FrameKind.NODE_OUTPUT.value, 1),
            2,
            GraphExecutionAttemptId("attempt"),
            "codec",
            1,
            (CURSOR.definition_id, CURSOR.definition_version, CURSOR.revision, CURSOR.digest),
        ),
        b"publication-payload",
    )

    assert record.evidence.digest == expected
    assert record.evidence != graph_input().evidence


INPUT_MUTATIONS: tuple[Callable[[PersistedGraphInput[str]], PersistedGraphInput[str]], ...] = (
    lambda item: replace(item, frame=replace(item.frame, codec_id="another-codec")),
    lambda item: replace(item, frame=replace(item.frame, codec_version=2)),
    lambda item: replace(item, frame=replace(item.frame, payload=b"different")),
    lambda item: replace(item, frame=replace(item.frame, config_cursor=None)),
    lambda item: replace(
        item,
        coordinate=replace(item.coordinate, scope_run=ScopeRunCoordinate((GraphNodeId("child"),), RUN_ID)),
    ),
    lambda item: replace(
        item,
        coordinate=replace(item.coordinate, descriptor=replace(item.coordinate.descriptor, owner_ordinal=2)),
    ),
    lambda item: replace(item, birth=GraphCommitKey(RUN_ID, 1)),
    lambda item: replace(item, evidence=GraphEvidenceCommitment(b"x" * 32)),
)


@pytest.mark.parametrize("mutate", INPUT_MUTATIONS)
def test_graph_input_rejects_every_fact_change_with_the_original_commitment(
    mutate: Callable[[PersistedGraphInput[str]], PersistedGraphInput[str]],
) -> None:
    with pytest.raises(SnapshotMismatchError):
        mutate(graph_input())


PUBLICATION_MUTATIONS: tuple[Callable[[PersistedPublication[str]], PersistedPublication[str]], ...] = (
    lambda item: replace(item, frame=replace(item.frame, codec_id="another-codec")),
    lambda item: replace(item, frame=replace(item.frame, codec_version=2)),
    lambda item: replace(item, frame=replace(item.frame, payload=b"different")),
    lambda item: replace(item, frame=replace(item.frame, config_cursor=None)),
    lambda item: replace(
        item,
        coordinate=replace(item.coordinate, activation=replace(item.coordinate.activation, superstep=4)),
    ),
    lambda item: replace(
        item,
        coordinate=replace(
            item.coordinate,
            activation=replace(item.coordinate.activation, node_id=GraphNodeId("other")),
        ),
    ),
    lambda item: replace(
        item,
        coordinate=replace(item.coordinate, descriptor=replace(item.coordinate.descriptor, owner_ordinal=2)),
    ),
    lambda item: replace(item, birth=replace(item.birth, revision=item.birth.revision + 1)),
    lambda item: replace(
        item,
        provenance=ExecutionPublicationProvenance(
            replace(item.provenance.execution_token, attempt_id=GraphExecutionAttemptId("other")),
        ),
    ),
    lambda item: replace(item, evidence=GraphEvidenceCommitment(b"x" * 32)),
)


@pytest.mark.parametrize("mutate", PUBLICATION_MUTATIONS)
def test_publication_rejects_every_fact_change_with_the_original_commitment(
    mutate: Callable[[PersistedPublication[str]], PersistedPublication[str]],
) -> None:
    with pytest.raises(SnapshotMismatchError):
        mutate(publication())


def test_sibling_payloads_cannot_be_reassigned_by_rebuilding_only_the_frame() -> None:
    first = publication(node_id="first", payload=b"first")
    second = publication(node_id="second", payload=b"second", revision=8)

    with pytest.raises(SnapshotMismatchError):
        replace(first, frame=EncodedFrame("codec", 1, second.frame.payload, CURSOR))


@pytest.mark.parametrize("cursor", [None, CURSOR], ids=["absent", "present"])
def test_config_presence_is_part_of_the_complete_record_commitment(cursor: GraphConfigCursor | None) -> None:
    record = graph_input(cursor=cursor)
    replacement = CURSOR if cursor is None else None

    with pytest.raises(SnapshotMismatchError):
        replace(record, frame=replace(record.frame, config_cursor=replacement))

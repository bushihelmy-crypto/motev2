from dataclasses import replace
from hashlib import sha256

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.persistence import EncodedFrame
from mote_kernel.state.graph_state import GraphConfigCursor, GraphDefinitionId, GraphDefinitionVersion

CURSOR = GraphConfigCursor(GraphDefinitionId('配置"\\definition'), GraphDefinitionVersion(3), 2, "snapshot-digest")


@pytest.mark.parametrize(
    ("cursor", "metadata"),
    [
        (None, b'["codec",1,null]'),
        (CURSOR, b'["codec",1,["\\u914d\\u7f6e\\"\\\\definition",3,2,"snapshot-digest"]]'),
    ],
    ids=["absent", "present"],
)
def test_frame_digest_has_one_canonical_domain_separated_encoding(
    cursor: GraphConfigCursor | None, metadata: bytes
) -> None:
    payload = b"\x00\xffbusiness\x00payload"
    frame = EncodedFrame.capture("codec", 1, payload, cursor)
    assert frame.frame_digest == sha256(b"mote.graph-frame\x00" + metadata + b"\x00" + payload).hexdigest()
    assert replace(frame) == frame


@pytest.mark.parametrize(
    "changes",
    [
        {"codec_id": "another-codec"},
        {"codec_version": 2},
        {"payload": b"different"},
        {"config_cursor": None},
        {"config_cursor": replace(CURSOR, definition_id=GraphDefinitionId("another-config"))},
        {"config_cursor": replace(CURSOR, definition_version=GraphDefinitionVersion(4))},
        {"config_cursor": replace(CURSOR, revision=3)},
        {"config_cursor": replace(CURSOR, digest="another-digest")},
    ],
    ids=[
        "codec-id",
        "codec-version",
        "payload",
        "deleted-cursor",
        "config-definition",
        "config-version",
        "config-revision",
        "config-digest",
    ],
)
def test_frame_rejects_every_metadata_change_with_the_original_digest(changes: dict[str, object]) -> None:
    frame = EncodedFrame.capture("codec", 1, b"payload", CURSOR)
    with pytest.raises(Graph.SnapshotMismatchError, match="integrity"):
        replace(frame, **changes)


def test_absent_config_is_also_committed_and_cannot_be_injected() -> None:
    frame = EncodedFrame.capture("codec", 1, b"payload")
    with pytest.raises(Graph.SnapshotMismatchError, match="integrity"):
        replace(frame, config_cursor=CURSOR)


@pytest.mark.parametrize("cursor", [None, CURSOR], ids=["absent", "present"])
def test_payload_only_digest_is_not_an_alternate_integrity_contract(cursor: GraphConfigCursor | None) -> None:
    payload = b"payload"
    with pytest.raises(Graph.SnapshotMismatchError, match="integrity"):
        EncodedFrame("codec", 1, payload, sha256(payload).hexdigest(), cursor)

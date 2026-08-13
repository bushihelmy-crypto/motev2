from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from mote_kernel.state.graph_state import (
    AcquireResources,
    GraphNodeId,
    ReleaseResources,
    ResourceAcquisition,
    ResourceCommand,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
    ResourceTransitionError,
    reduce_resources,
    validate_resource_snapshot,
)

FILE = ResourceId("file")
DATABASE = ResourceId("database")
A = GraphNodeId("a")
B = GraphNodeId("b")
C = GraphNodeId("c")


def empty_snapshot() -> ResourceSnapshot:
    return ResourceSnapshot((ResourceLock(FILE), ResourceLock(DATABASE)))


def test_acquire_takes_available_resources_in_global_order() -> None:
    old = empty_snapshot()

    new = reduce_resources(old, AcquireResources(A, (FILE, DATABASE)))

    assert old == empty_snapshot()
    assert new.resources == (ResourceLock(FILE, A), ResourceLock(DATABASE, A))
    assert new.acquisitions[0].admitted
    with pytest.raises(FrozenInstanceError):
        new.resources[0].owner = B  # type: ignore[misc]


def test_contender_waits_fifo_for_its_first_unavailable_resource() -> None:
    owned = reduce_resources(empty_snapshot(), AcquireResources(A, (FILE, DATABASE)))

    waiting = reduce_resources(owned, AcquireResources(B, (FILE, DATABASE)))

    assert waiting.resources[0] == ResourceLock(FILE, A, (B,))
    assert waiting.acquisitions[1].acquired == ()
    assert waiting.acquisitions[1].waiting_for == FILE
    assert not waiting.acquisitions[1].admitted


def test_release_wakes_fifo_head_and_advances_its_available_prefix() -> None:
    state = reduce_resources(empty_snapshot(), AcquireResources(A, (FILE, DATABASE)))
    state = reduce_resources(state, AcquireResources(B, (FILE, DATABASE)))

    released = reduce_resources(state, ReleaseResources(A))

    assert released.resources == (ResourceLock(FILE, B), ResourceLock(DATABASE, B))
    assert tuple(item.node_id for item in released.acquisitions) == (B,)
    assert released.acquisitions[0].admitted


def test_three_contenders_are_admitted_in_fifo_order() -> None:
    state = reduce_resources(empty_snapshot(), AcquireResources(A, (FILE,)))
    state = reduce_resources(state, AcquireResources(B, (FILE,)))
    state = reduce_resources(state, AcquireResources(C, (FILE,)))

    after_a = reduce_resources(state, ReleaseResources(A))
    after_b = reduce_resources(after_a, ReleaseResources(B))

    assert after_a.resources[0] == ResourceLock(FILE, B, (C,))
    assert after_b.resources[0] == ResourceLock(FILE, C)
    assert after_b.acquisitions[0].node_id == C
    assert after_b.acquisitions[0].admitted


def test_partially_held_task_keeps_prefix_until_later_resource_releases() -> None:
    database_owner = reduce_resources(empty_snapshot(), AcquireResources(A, (DATABASE,)))

    waiting = reduce_resources(database_owner, AcquireResources(B, (FILE, DATABASE)))

    assert waiting.resources == (ResourceLock(FILE, B), ResourceLock(DATABASE, A, (B,)))
    assert waiting.acquisitions[1] == ResourceAcquisition(B, (FILE, DATABASE), (FILE,), DATABASE)

    admitted = reduce_resources(waiting, ReleaseResources(A))

    assert admitted.resources == (ResourceLock(FILE, B), ResourceLock(DATABASE, B))
    assert admitted.acquisitions[0].admitted


@pytest.mark.parametrize(
    "command",
    [
        AcquireResources(A, (DATABASE, FILE)),
        AcquireResources(A, (FILE, FILE)),
        AcquireResources(A, (ResourceId("unknown"),)),
        AcquireResources(GraphNodeId(""), (FILE,)),
        AcquireResources(GraphNodeId("bad\nnode"), (FILE,)),
    ],
)
def test_invalid_acquisition_fails_closed(command: AcquireResources) -> None:
    with pytest.raises(ResourceTransitionError):
        reduce_resources(empty_snapshot(), command)


def test_participant_cannot_acquire_twice_or_release_while_waiting() -> None:
    state = reduce_resources(empty_snapshot(), AcquireResources(A, (FILE,)))
    with pytest.raises(ResourceTransitionError, match="already"):
        reduce_resources(state, AcquireResources(A, (DATABASE,)))

    state = reduce_resources(state, AcquireResources(B, (FILE,)))
    with pytest.raises(ResourceTransitionError, match="admitted"):
        reduce_resources(state, ReleaseResources(B))


def test_empty_resource_request_is_a_noop_and_missing_release_fails() -> None:
    snapshot = empty_snapshot()

    assert reduce_resources(snapshot, AcquireResources(A, ())) is snapshot
    with pytest.raises(ResourceTransitionError, match="no acquisition"):
        reduce_resources(snapshot, ReleaseResources(A))
    with pytest.raises(ResourceTransitionError, match="identity"):
        reduce_resources(snapshot, ReleaseResources(GraphNodeId(" ")))
    with pytest.raises(ResourceTransitionError, match="unsupported variant"):
        reduce_resources(snapshot, cast(ResourceCommand, object()))


@pytest.mark.parametrize(
    "snapshot",
    [
        ResourceSnapshot((ResourceLock(FILE), ResourceLock(FILE))),
        ResourceSnapshot((ResourceLock(ResourceId("")),)),
        ResourceSnapshot((ResourceLock(ResourceId("bad\nresource")),)),
        ResourceSnapshot((ResourceLock(FILE, waiters=(A, A)),)),
        ResourceSnapshot((ResourceLock(FILE, A, (A,)),), (ResourceAcquisition(A, (FILE,), (FILE,)),)),
        ResourceSnapshot((ResourceLock(FILE, GraphNodeId("")),)),
        ResourceSnapshot((ResourceLock(FILE, waiters=(GraphNodeId(""),)),)),
        ResourceSnapshot(
            (ResourceLock(FILE, A),),
            (ResourceAcquisition(A, (FILE,), (FILE,)), ResourceAcquisition(A, (FILE,), (FILE,))),
        ),
        ResourceSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (), ()),)),
        ResourceSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (FILE, FILE), ()),)),
        ResourceSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (ResourceId("unknown"),), ()),)),
        ResourceSnapshot(
            (ResourceLock(FILE), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (DATABASE, FILE), ()),),
        ),
        ResourceSnapshot(
            (ResourceLock(FILE), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (FILE, DATABASE), (DATABASE,)),),
        ),
        ResourceSnapshot(
            (ResourceLock(FILE), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (FILE, DATABASE), (), FILE),),
        ),
        ResourceSnapshot(
            (ResourceLock(FILE, A), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (FILE, DATABASE), (FILE,), FILE),),
        ),
        ResourceSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (FILE,), (FILE,)),)),
        ResourceSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (FILE,), (), FILE),)),
        ResourceSnapshot((ResourceLock(FILE, A),)),
        ResourceSnapshot((ResourceLock(FILE, waiters=(A,)),)),
    ],
)
def test_corrupt_snapshot_fails_closed(snapshot: ResourceSnapshot) -> None:
    with pytest.raises(ResourceTransitionError):
        reduce_resources(snapshot, AcquireResources(B, ()))


def test_release_revalidates_ownership_before_transition() -> None:
    state = reduce_resources(empty_snapshot(), AcquireResources(A, (FILE,)))
    object.__setattr__(state.resources[0], "owner", B)

    with pytest.raises(ResourceTransitionError):
        reduce_resources(state, ReleaseResources(A))


def test_waiter_must_be_queued_on_the_resource_it_is_waiting_for() -> None:
    snapshot = ResourceSnapshot(
        (ResourceLock(FILE, A, waiters=(B,)), ResourceLock(DATABASE, waiters=(B,))),
        (
            ResourceAcquisition(A, (FILE,), (FILE,)),
            ResourceAcquisition(B, (FILE, DATABASE), (), FILE),
        ),
    )

    with pytest.raises(ResourceTransitionError, match="waiting elsewhere"):
        reduce_resources(snapshot, AcquireResources(GraphNodeId("c"), ()))


def test_snapshot_validation_rejects_non_fifo_history_that_looks_structurally_valid() -> None:
    snapshot = ResourceSnapshot(
        (ResourceLock(FILE, A, (B,)),),
        (
            ResourceAcquisition(B, (FILE,), (), FILE),
            ResourceAcquisition(A, (FILE,), (FILE,)),
        ),
    )

    with pytest.raises(ResourceTransitionError, match="replayed acquisition sequence"):
        validate_resource_snapshot(snapshot)
    with pytest.raises(ResourceTransitionError, match="replayed acquisition sequence"):
        reduce_resources(snapshot, AcquireResources(C, ()))

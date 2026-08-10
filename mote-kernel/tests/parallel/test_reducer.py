from dataclasses import FrozenInstanceError

import pytest

from mote_kernel.parallel import (
    AcquireResources,
    ParallelSnapshot,
    ParallelTransitionError,
    ParticipantId,
    ReleaseResources,
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    reduce_parallel,
)

FILE = ResourceId("file")
DATABASE = ResourceId("database")
A = ParticipantId("a")
B = ParticipantId("b")
C = ParticipantId("c")


def empty_snapshot() -> ParallelSnapshot:
    return ParallelSnapshot((ResourceLock(FILE), ResourceLock(DATABASE)))


def test_acquire_takes_available_resources_in_global_order() -> None:
    old = empty_snapshot()

    new = reduce_parallel(old, AcquireResources(A, (FILE, DATABASE)))

    assert old == empty_snapshot()
    assert new.resources == (ResourceLock(FILE, A), ResourceLock(DATABASE, A))
    assert new.acquisitions[0].admitted
    with pytest.raises(FrozenInstanceError):
        new.resources[0].owner = B  # type: ignore[misc]


def test_contender_waits_fifo_for_its_first_unavailable_resource() -> None:
    owned = reduce_parallel(empty_snapshot(), AcquireResources(A, (FILE, DATABASE)))

    waiting = reduce_parallel(owned, AcquireResources(B, (FILE, DATABASE)))

    assert waiting.resources[0] == ResourceLock(FILE, A, (B,))
    assert waiting.acquisitions[1].acquired == ()
    assert waiting.acquisitions[1].waiting_for == FILE
    assert not waiting.acquisitions[1].admitted


def test_release_wakes_fifo_head_and_advances_its_available_prefix() -> None:
    state = reduce_parallel(empty_snapshot(), AcquireResources(A, (FILE, DATABASE)))
    state = reduce_parallel(state, AcquireResources(B, (FILE, DATABASE)))

    released = reduce_parallel(state, ReleaseResources(A))

    assert released.resources == (ResourceLock(FILE, B), ResourceLock(DATABASE, B))
    assert tuple(item.participant_id for item in released.acquisitions) == (B,)
    assert released.acquisitions[0].admitted


def test_three_contenders_are_admitted_in_fifo_order() -> None:
    state = reduce_parallel(empty_snapshot(), AcquireResources(A, (FILE,)))
    state = reduce_parallel(state, AcquireResources(B, (FILE,)))
    state = reduce_parallel(state, AcquireResources(C, (FILE,)))

    after_a = reduce_parallel(state, ReleaseResources(A))
    after_b = reduce_parallel(after_a, ReleaseResources(B))

    assert after_a.resources[0] == ResourceLock(FILE, B, (C,))
    assert after_b.resources[0] == ResourceLock(FILE, C)
    assert after_b.acquisitions[0].participant_id == C
    assert after_b.acquisitions[0].admitted


def test_partially_held_task_keeps_prefix_until_later_resource_releases() -> None:
    database_owner = reduce_parallel(empty_snapshot(), AcquireResources(A, (DATABASE,)))

    waiting = reduce_parallel(database_owner, AcquireResources(B, (FILE, DATABASE)))

    assert waiting.resources == (ResourceLock(FILE, B), ResourceLock(DATABASE, A, (B,)))
    assert waiting.acquisitions[1] == ResourceAcquisition(B, (FILE, DATABASE), (FILE,), DATABASE)

    admitted = reduce_parallel(waiting, ReleaseResources(A))

    assert admitted.resources == (ResourceLock(FILE, B), ResourceLock(DATABASE, B))
    assert admitted.acquisitions[0].admitted


@pytest.mark.parametrize(
    "command",
    [
        AcquireResources(A, (DATABASE, FILE)),
        AcquireResources(A, (FILE, FILE)),
        AcquireResources(A, (ResourceId("unknown"),)),
        AcquireResources(ParticipantId(""), (FILE,)),
    ],
)
def test_invalid_acquisition_fails_closed(command: AcquireResources) -> None:
    with pytest.raises(ParallelTransitionError):
        reduce_parallel(empty_snapshot(), command)


def test_participant_cannot_acquire_twice_or_release_while_waiting() -> None:
    state = reduce_parallel(empty_snapshot(), AcquireResources(A, (FILE,)))
    with pytest.raises(ParallelTransitionError, match="already"):
        reduce_parallel(state, AcquireResources(A, (DATABASE,)))

    state = reduce_parallel(state, AcquireResources(B, (FILE,)))
    with pytest.raises(ParallelTransitionError, match="admitted"):
        reduce_parallel(state, ReleaseResources(B))


def test_empty_resource_request_is_a_noop_and_missing_release_fails() -> None:
    snapshot = empty_snapshot()

    assert reduce_parallel(snapshot, AcquireResources(A, ())) is snapshot
    with pytest.raises(ParallelTransitionError, match="no acquisition"):
        reduce_parallel(snapshot, ReleaseResources(A))
    with pytest.raises(ParallelTransitionError, match="identity"):
        reduce_parallel(snapshot, ReleaseResources(ParticipantId(" ")))


@pytest.mark.parametrize(
    "snapshot",
    [
        ParallelSnapshot((ResourceLock(FILE), ResourceLock(FILE))),
        ParallelSnapshot((ResourceLock(ResourceId("")),)),
        ParallelSnapshot((ResourceLock(FILE, waiters=(A, A)),)),
        ParallelSnapshot((ResourceLock(FILE, A, (A,)),), (ResourceAcquisition(A, (FILE,), (FILE,)),)),
        ParallelSnapshot((ResourceLock(FILE, ParticipantId("")),)),
        ParallelSnapshot((ResourceLock(FILE, waiters=(ParticipantId(""),)),)),
        ParallelSnapshot(
            (ResourceLock(FILE, A),),
            (ResourceAcquisition(A, (FILE,), (FILE,)), ResourceAcquisition(A, (FILE,), (FILE,))),
        ),
        ParallelSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (), ()),)),
        ParallelSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (FILE, FILE), ()),)),
        ParallelSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (ResourceId("unknown"),), ()),)),
        ParallelSnapshot(
            (ResourceLock(FILE), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (DATABASE, FILE), ()),),
        ),
        ParallelSnapshot(
            (ResourceLock(FILE), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (FILE, DATABASE), (DATABASE,)),),
        ),
        ParallelSnapshot(
            (ResourceLock(FILE), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (FILE, DATABASE), (), FILE),),
        ),
        ParallelSnapshot(
            (ResourceLock(FILE, A), ResourceLock(DATABASE)),
            (ResourceAcquisition(A, (FILE, DATABASE), (FILE,), FILE),),
        ),
        ParallelSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (FILE,), (FILE,)),)),
        ParallelSnapshot((ResourceLock(FILE),), (ResourceAcquisition(A, (FILE,), (), FILE),)),
        ParallelSnapshot((ResourceLock(FILE, A),)),
        ParallelSnapshot((ResourceLock(FILE, waiters=(A,)),)),
    ],
)
def test_corrupt_snapshot_fails_closed(snapshot: ParallelSnapshot) -> None:
    with pytest.raises(ParallelTransitionError):
        reduce_parallel(snapshot, AcquireResources(B, ()))


def test_release_revalidates_ownership_before_transition() -> None:
    state = reduce_parallel(empty_snapshot(), AcquireResources(A, (FILE,)))
    object.__setattr__(state.resources[0], "owner", B)

    with pytest.raises(ParallelTransitionError):
        reduce_parallel(state, ReleaseResources(A))


def test_waiter_must_be_queued_on_the_resource_it_is_waiting_for() -> None:
    snapshot = ParallelSnapshot(
        (ResourceLock(FILE, A, waiters=(B,)), ResourceLock(DATABASE, waiters=(B,))),
        (
            ResourceAcquisition(A, (FILE,), (FILE,)),
            ResourceAcquisition(B, (FILE, DATABASE), (), FILE),
        ),
    )

    with pytest.raises(ParallelTransitionError, match="waiting elsewhere"):
        reduce_parallel(snapshot, AcquireResources(ParticipantId("c"), ()))

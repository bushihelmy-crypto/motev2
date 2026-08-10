"""Pure transitions for ordered exclusive-resource acquisition."""

from dataclasses import replace

from mote_kernel.parallel.command import AcquireResources, ParallelCommand
from mote_kernel.parallel.definition import ResourceId
from mote_kernel.parallel.model import ParallelSnapshot, ParticipantId, ResourceAcquisition, ResourceLock


class ParallelTransitionError(ValueError):
    """A resource command is invalid for the supplied snapshot."""


def _require_identity(value: str, kind: str) -> None:
    if not value or value != value.strip():
        raise ParallelTransitionError(f"{kind} identity must be non-empty and trimmed")


def _validate_snapshot(snapshot: ParallelSnapshot) -> None:
    resource_ids = tuple(resource.resource_id for resource in snapshot.resources)
    if len(resource_ids) != len(frozenset(resource_ids)):
        raise ParallelTransitionError("parallel snapshot repeats a resource")
    for resource in snapshot.resources:
        _require_identity(resource.resource_id, "resource")
        if len(resource.waiters) != len(frozenset(resource.waiters)):
            raise ParallelTransitionError("resource waiters must be unique")
        if resource.owner is not None:
            _require_identity(resource.owner, "participant")
            if resource.owner in resource.waiters:
                raise ParallelTransitionError("resource owner cannot also be waiting")
        for waiter in resource.waiters:
            _require_identity(waiter, "participant")

    acquisitions = {acquisition.participant_id: acquisition for acquisition in snapshot.acquisitions}
    if len(acquisitions) != len(snapshot.acquisitions):
        raise ParallelTransitionError("parallel snapshot repeats an acquisition")
    positions = {resource_id: position for position, resource_id in enumerate(resource_ids)}
    known_resources = frozenset(resource_ids)
    for acquisition in snapshot.acquisitions:
        _require_identity(acquisition.participant_id, "participant")
        if not acquisition.required:
            raise ParallelTransitionError("an acquisition requires at least one resource")
        if len(acquisition.required) != len(frozenset(acquisition.required)):
            raise ParallelTransitionError("an acquisition repeats a resource")
        if not frozenset(acquisition.required) <= known_resources:
            raise ParallelTransitionError("an acquisition references an unknown resource")
        if tuple(sorted(acquisition.required, key=positions.__getitem__)) != acquisition.required:
            raise ParallelTransitionError("resources must be acquired in snapshot order")
        if acquisition.acquired != acquisition.required[: len(acquisition.acquired)]:
            raise ParallelTransitionError("acquired resources must be a required-resource prefix")
        expected_waiting = (
            acquisition.required[len(acquisition.acquired)]
            if len(acquisition.acquired) < len(acquisition.required)
            else None
        )
        if acquisition.waiting_for != expected_waiting:
            raise ParallelTransitionError("an acquisition can only wait for its next resource")
        for resource_id in acquisition.acquired:
            resource = snapshot.resources[positions[resource_id]]
            if resource.owner != acquisition.participant_id:
                raise ParallelTransitionError("acquired resource ownership does not match its acquisition")
        if acquisition.waiting_for is not None:
            resource = snapshot.resources[positions[acquisition.waiting_for]]
            if acquisition.participant_id not in resource.waiters:
                raise ParallelTransitionError("waiting acquisition is absent from the resource queue")

    participants = frozenset(acquisitions)
    for resource in snapshot.resources:
        if resource.owner is not None and resource.owner not in participants:
            raise ParallelTransitionError("resource owner has no acquisition")
        if not frozenset(resource.waiters) <= participants:
            raise ParallelTransitionError("resource waiter has no acquisition")
        if any(acquisitions[waiter].waiting_for != resource.resource_id for waiter in resource.waiters):
            raise ParallelTransitionError("resource queue contains a participant waiting elsewhere")


def _advance(
    resources: list[ResourceLock], acquisition: ResourceAcquisition, positions: dict[ResourceId, int]
) -> ResourceAcquisition:
    acquired = list(acquisition.acquired)
    while len(acquired) < len(acquisition.required):
        next_resource_id = acquisition.required[len(acquired)]
        position = positions[next_resource_id]
        resource = resources[position]
        can_acquire = resource.owner is None and (
            not resource.waiters or resource.waiters[0] == acquisition.participant_id
        )
        if not can_acquire:
            resources[position] = replace(resource, waiters=(*resource.waiters, acquisition.participant_id))
            return replace(acquisition, acquired=tuple(acquired), waiting_for=next_resource_id)
        waiters = resource.waiters[1:] if resource.waiters else ()
        resources[position] = replace(resource, owner=acquisition.participant_id, waiters=waiters)
        acquired.append(next_resource_id)
    return replace(acquisition, acquired=tuple(acquired), waiting_for=None)


def _acquire(snapshot: ParallelSnapshot, command: AcquireResources) -> ParallelSnapshot:
    _require_identity(command.participant_id, "participant")
    if not command.resources:
        return snapshot
    if any(acquisition.participant_id == command.participant_id for acquisition in snapshot.acquisitions):
        raise ParallelTransitionError("participant already has an acquisition")
    positions = {resource.resource_id: position for position, resource in enumerate(snapshot.resources)}
    if len(command.resources) != len(frozenset(command.resources)):
        raise ParallelTransitionError("resource request contains duplicates")
    if not frozenset(command.resources) <= frozenset(positions):
        raise ParallelTransitionError("resource request references an unknown resource")
    if tuple(sorted(command.resources, key=positions.__getitem__)) != command.resources:
        raise ParallelTransitionError("resource request violates the global resource order")

    resources = list(snapshot.resources)
    acquisition = _advance(
        resources,
        ResourceAcquisition(command.participant_id, command.resources, ()),
        positions,
    )
    return ParallelSnapshot(tuple(resources), (*snapshot.acquisitions, acquisition))


def _release(snapshot: ParallelSnapshot, participant_id: ParticipantId) -> ParallelSnapshot:
    acquisition = next(
        (item for item in snapshot.acquisitions if item.participant_id == participant_id),
        None,
    )
    if acquisition is None:
        raise ParallelTransitionError("participant has no acquisition to release")
    if not acquisition.admitted:
        raise ParallelTransitionError("only an admitted participant can release resources")

    positions = {resource.resource_id: position for position, resource in enumerate(snapshot.resources)}
    resources = list(snapshot.resources)
    for resource_id in reversed(acquisition.acquired):
        position = positions[resource_id]
        resource = resources[position]
        resources[position] = replace(resource, owner=None)

    acquisitions = [item for item in snapshot.acquisitions if item.participant_id != participant_id]
    by_participant = {item.participant_id: item for item in acquisitions}
    for resource in tuple(resources):
        position = positions[resource.resource_id]
        current = resources[position]
        if current.owner is None and current.waiters:
            waiter = current.waiters[0]
            by_participant[waiter] = _advance(resources, by_participant[waiter], positions)
    return ParallelSnapshot(
        tuple(resources),
        tuple(by_participant[item.participant_id] for item in acquisitions),
    )


def reduce_parallel(snapshot: ParallelSnapshot, command: ParallelCommand) -> ParallelSnapshot:
    """Return a new resource snapshot without mutating the prior snapshot."""

    _validate_snapshot(snapshot)
    if isinstance(command, AcquireResources):
        result = _acquire(snapshot, command)
    else:
        _require_identity(command.participant_id, "participant")
        result = _release(snapshot, command.participant_id)
    _validate_snapshot(result)
    return result


def validate_parallel_snapshot(snapshot: ParallelSnapshot) -> None:
    """Reject a corrupt resource snapshot without changing it."""

    _validate_snapshot(snapshot)


__all__ = ["ParallelTransitionError", "reduce_parallel", "validate_parallel_snapshot"]

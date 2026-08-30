"""Pure transitions for ordered exclusive-resource acquisition."""

from dataclasses import replace

from mote_kernel.state.graph_state.identity import GraphNodeId
from mote_kernel.state.graph_state.resource_command import AcquireResources, ReleaseResources, ResourceCommand
from mote_kernel.state.graph_state.resource_model import (
    ResourceAcquisition,
    ResourceId,
    ResourceLock,
    ResourceSnapshot,
)


class ResourceTransitionError(ValueError):
    """A resource command is invalid for the supplied snapshot."""


def _require_identity(value: str, kind: str) -> None:
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ResourceTransitionError(f"{kind} identity must be non-empty and trimmed")


def _validate_snapshot(snapshot: ResourceSnapshot) -> None:
    resource_ids = tuple(resource.resource_id for resource in snapshot.resources)
    if len(resource_ids) != len(frozenset(resource_ids)):
        raise ResourceTransitionError("resources snapshot repeats a resource")
    for resource in snapshot.resources:
        _require_identity(resource.resource_id, "resource")
        if len(resource.waiters) != len(frozenset(resource.waiters)):
            raise ResourceTransitionError("resource waiters must be unique")
        if resource.owner is not None:
            _require_identity(resource.owner, "node")
            if resource.owner in resource.waiters:
                raise ResourceTransitionError("resource owner cannot also be waiting")
        for waiter in resource.waiters:
            _require_identity(waiter, "node")

    acquisitions = {acquisition.node_id: acquisition for acquisition in snapshot.acquisitions}
    if len(acquisitions) != len(snapshot.acquisitions):
        raise ResourceTransitionError("resources snapshot repeats an acquisition")
    positions = {resource_id: position for position, resource_id in enumerate(resource_ids)}
    known_resources = frozenset(resource_ids)
    for acquisition in snapshot.acquisitions:
        _require_identity(acquisition.node_id, "node")
        if not acquisition.required:
            raise ResourceTransitionError("an acquisition requires at least one resource")
        if len(acquisition.required) != len(frozenset(acquisition.required)):
            raise ResourceTransitionError("an acquisition repeats a resource")
        if not frozenset(acquisition.required) <= known_resources:
            raise ResourceTransitionError("an acquisition references an unknown resource")
        if tuple(sorted(acquisition.required, key=positions.__getitem__)) != acquisition.required:
            raise ResourceTransitionError("resources must be acquired in snapshot order")
        if acquisition.acquired != acquisition.required[: len(acquisition.acquired)]:
            raise ResourceTransitionError("acquired resources must be a required-resource prefix")
        expected_waiting = (
            acquisition.required[len(acquisition.acquired)]
            if len(acquisition.acquired) < len(acquisition.required)
            else None
        )
        if acquisition.waiting_for != expected_waiting:
            raise ResourceTransitionError("an acquisition can only wait for its next resource")
        for resource_id in acquisition.acquired:
            resource = snapshot.resources[positions[resource_id]]
            if resource.owner != acquisition.node_id:
                raise ResourceTransitionError("acquired resource ownership does not match its acquisition")
        if acquisition.waiting_for is not None:
            resource = snapshot.resources[positions[acquisition.waiting_for]]
            if acquisition.node_id not in resource.waiters:
                raise ResourceTransitionError("waiting acquisition is absent from the resource queue")

    participants = frozenset(acquisitions)
    for resource in snapshot.resources:
        if resource.owner is not None and resource.owner not in participants:
            raise ResourceTransitionError("resource owner has no acquisition")
        if not frozenset(resource.waiters) <= participants:
            raise ResourceTransitionError("resource waiter has no acquisition")
        if any(acquisitions[waiter].waiting_for != resource.resource_id for waiter in resource.waiters):
            raise ResourceTransitionError("resource queue contains a participant waiting elsewhere")


def _advance(
    resources: list[ResourceLock], acquisition: ResourceAcquisition, positions: dict[ResourceId, int]
) -> ResourceAcquisition:
    acquired = list(acquisition.acquired)
    while len(acquired) < len(acquisition.required):
        next_resource_id = acquisition.required[len(acquired)]
        position = positions[next_resource_id]
        resource = resources[position]
        can_acquire = resource.owner is None and (not resource.waiters or resource.waiters[0] == acquisition.node_id)
        if not can_acquire:
            resources[position] = replace(resource, waiters=(*resource.waiters, acquisition.node_id))
            return replace(acquisition, acquired=tuple(acquired), waiting_for=next_resource_id)
        waiters = resource.waiters[1:] if resource.waiters else ()
        resources[position] = replace(resource, owner=acquisition.node_id, waiters=waiters)
        acquired.append(next_resource_id)
    return replace(acquisition, acquired=tuple(acquired), waiting_for=None)


def _acquire(snapshot: ResourceSnapshot, command: AcquireResources) -> ResourceSnapshot:
    _require_identity(command.node_id, "node")
    if not command.resources:
        return snapshot
    if any(acquisition.node_id == command.node_id for acquisition in snapshot.acquisitions):
        raise ResourceTransitionError("node already has an acquisition")
    positions = {resource.resource_id: position for position, resource in enumerate(snapshot.resources)}
    if len(command.resources) != len(frozenset(command.resources)):
        raise ResourceTransitionError("resource request contains duplicates")
    if not frozenset(command.resources) <= frozenset(positions):
        raise ResourceTransitionError("resource request references an unknown resource")
    if tuple(sorted(command.resources, key=positions.__getitem__)) != command.resources:
        raise ResourceTransitionError("resource request violates the global resource order")

    resources = list(snapshot.resources)
    acquisition = _advance(
        resources,
        ResourceAcquisition(command.node_id, command.resources, ()),
        positions,
    )
    return ResourceSnapshot(tuple(resources), (*snapshot.acquisitions, acquisition))


def _release(snapshot: ResourceSnapshot, node_id: GraphNodeId) -> ResourceSnapshot:
    acquisition = next(
        (item for item in snapshot.acquisitions if item.node_id == node_id),
        None,
    )
    if acquisition is None:
        raise ResourceTransitionError("node has no acquisition to release")
    if not acquisition.admitted:
        raise ResourceTransitionError("only an admitted participant can release resources")

    positions = {resource.resource_id: position for position, resource in enumerate(snapshot.resources)}
    resources = list(snapshot.resources)
    for resource_id in reversed(acquisition.acquired):
        position = positions[resource_id]
        resource = resources[position]
        resources[position] = replace(resource, owner=None)

    acquisitions = [item for item in snapshot.acquisitions if item.node_id != node_id]
    by_node = {item.node_id: item for item in acquisitions}
    for position in range(len(resources)):
        current = resources[position]
        if current.owner is None and current.waiters:
            waiter = current.waiters[0]
            by_node[waiter] = _advance(resources, by_node[waiter], positions)
    return ResourceSnapshot(
        tuple(resources),
        tuple(by_node[item.node_id] for item in acquisitions),
    )


def _apply_command(snapshot: ResourceSnapshot, command: ResourceCommand) -> ResourceSnapshot:
    match command:
        case AcquireResources():
            result = _acquire(snapshot, command)
        case ReleaseResources():
            _require_identity(command.node_id, "node")
            result = _release(snapshot, command.node_id)
        case _:
            raise ResourceTransitionError("resource command has an unsupported variant")
    _validate_snapshot(result)
    return result


def reduce_resources(snapshot: ResourceSnapshot, command: ResourceCommand) -> ResourceSnapshot:
    """Return a new resource snapshot without mutating the prior snapshot."""

    validate_resource_snapshot(snapshot)
    result = _apply_command(snapshot, command)
    validate_resource_snapshot(result)
    return result


def validate_resource_snapshot(snapshot: ResourceSnapshot) -> None:
    """Reject a corrupt resource snapshot without changing it."""

    _validate_snapshot(snapshot)
    replayed = ResourceSnapshot(tuple(ResourceLock(resource.resource_id) for resource in snapshot.resources))
    for acquisition in snapshot.acquisitions:
        replayed = _apply_command(
            replayed,
            AcquireResources(acquisition.node_id, acquisition.required),
        )
    if replayed != snapshot:
        raise ResourceTransitionError("resources snapshot does not match its replayed acquisition sequence")


__all__ = ["ResourceTransitionError", "reduce_resources", "validate_resource_snapshot"]

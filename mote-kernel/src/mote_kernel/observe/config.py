"""Observe-owned narrow configuration projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar, cast

from mote_kernel.config import Config, ConfigContractError, ConfigSlice, require_config_projection
from mote_kernel.hooks.identity import HookSlotId
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.contract import (
    HookStateProjection,
    ObserveHookCommand,
)
from mote_kernel.observe.port import (
    BackgroundTaskPort,
    ConfigObservationPort,
    ContextObservationPort,
    ObservationAckPort,
    ObservationQueuePort,
    ObservationResumePort,
)
from mote_kernel.state.graph_state.identity import GraphDefinitionId, GraphDefinitionVersion, is_canonical_identity

PriorityConfigT = TypeVar("PriorityConfigT")
ObserveStateT = TypeVar("ObserveStateT", bound=HookStateProjection)
ObserveCommandT = TypeVar("ObserveCommandT", bound=ObserveHookCommand)
ObserveCapabilityT = TypeVar("ObserveCapabilityT")


@dataclass(frozen=True, slots=True)
class ObserveConfig(ConfigSlice, Generic[PriorityConfigT, ObserveStateT, ObserveCommandT]):
    """Exactly the capabilities and identity declared by ``ObserveNode``."""

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    queue_port: ObservationQueuePort
    background_task_port: BackgroundTaskPort
    config_port: ConfigObservationPort
    context_port: ContextObservationPort
    ack_port: ObservationAckPort
    resume_port: ObservationResumePort
    admission: ObservePayloadAdmission
    hook_slot: HookSlotId

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.definition_id):
            raise ConfigContractError("ObserveConfig.definition_id must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ConfigContractError("ObserveConfig.definition_version must be positive")
        if type(self.admission) is not ObservePayloadAdmission:
            raise ConfigContractError("ObserveConfig.admission must be an ObservePayloadAdmission")
        if type(self.hook_slot) is not HookSlotId:
            raise ConfigContractError("ObserveConfig.hook_slot must be a HookSlotId")
        if (
            self.hook_slot.definition_id != self.definition_id
            or self.hook_slot.definition_version != self.definition_version
        ):
            raise ConfigContractError("ObserveConfig.hook_slot does not match the Observe definition")


_ObserveAggregate: TypeAlias = ObserveConfig[object, HookStateProjection, ObserveHookCommand]


def _observe_aggregate(config: Config, /) -> _ObserveAggregate:
    return cast(
        _ObserveAggregate,
        require_config_projection(config.observe, ObserveConfig, "Config.observe"),
    )


@dataclass(frozen=True, slots=True)
class ObserveNodeConfig(ConfigSlice, Generic[ObserveCapabilityT]):
    """One Observe node's exact capability set and shared admission contract."""

    capability: ObserveCapabilityT
    admission: ObservePayloadAdmission

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if self.capability is None:
            raise ConfigContractError("Observe node projection requires its declared capability")
        if type(self.admission) is not ObservePayloadAdmission:
            raise ConfigContractError("Observe node projection requires an ObservePayloadAdmission")


@dataclass(frozen=True, slots=True)
class GetObservationCapabilities:
    queue_port: ObservationQueuePort
    background_task_port: BackgroundTaskPort


@dataclass(frozen=True, slots=True)
class WriteObservationCapabilities:
    config_port: ConfigObservationPort
    context_port: ContextObservationPort


@dataclass(frozen=True, slots=True)
class GetObservationBinding:
    """Get node's queue and background-snapshot declaration."""

    def select(self, config: Config, /) -> ObserveNodeConfig[GetObservationCapabilities]:
        selected = _observe_aggregate(config)
        return ObserveNodeConfig(
            selected.snapshot_key,
            GetObservationCapabilities(selected.queue_port, selected.background_task_port),
            selected.admission,
        )


@dataclass(frozen=True, slots=True)
class WriteObservationBinding:
    """Write node's Config and Context settlement declaration."""

    def select(self, config: Config, /) -> ObserveNodeConfig[WriteObservationCapabilities]:
        selected = _observe_aggregate(config)
        return ObserveNodeConfig(
            selected.snapshot_key,
            WriteObservationCapabilities(selected.config_port, selected.context_port),
            selected.admission,
        )


@dataclass(frozen=True, slots=True)
class AcknowledgeBinding:
    """Post-commit acknowledge operation's one-Port declaration."""

    def select(self, config: Config, /) -> ObserveNodeConfig[ObservationAckPort]:
        selected = _observe_aggregate(config)
        return ObserveNodeConfig(
            selected.snapshot_key,
            selected.ack_port,
            selected.admission,
        )


@dataclass(frozen=True, slots=True)
class ObserveBinding(Generic[PriorityConfigT, ObserveStateT, ObserveCommandT]):
    """Selector owned by ``ObserveNode``; it returns no other domain slice."""

    def select(self, config: Config, /) -> ObserveConfig[PriorityConfigT, ObserveStateT, ObserveCommandT]:
        return cast(
            ObserveConfig[PriorityConfigT, ObserveStateT, ObserveCommandT],
            _observe_aggregate(config),
        )


__all__ = ["ObserveBinding", "ObserveConfig"]

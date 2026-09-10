"""Act-owned narrow configuration projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import (
    ActHookCommand,
    HookStateProjection,
    OpaqueGraphFailureReason,
)
from mote_kernel.act.port import (
    AuthorizePort,
    ExecutePort,
    ResolvePort,
    SettlementPort,
    ToolExchangeWriter,
)
from mote_kernel.config import Config, ConfigContractError, ConfigSlice, require_config_projection
from mote_kernel.hooks.identity import HookSlotId
from mote_kernel.state.graph_state.identity import GraphDefinitionId, GraphDefinitionVersion, is_canonical_identity

ActStateT = TypeVar("ActStateT", bound=HookStateProjection)
ActHookCommandT = TypeVar("ActHookCommandT", bound=ActHookCommand)
ActCapabilityT = TypeVar("ActCapabilityT")


@dataclass(frozen=True, slots=True)
class ActConfig(ConfigSlice, Generic[ActStateT, ActHookCommandT]):
    """Exactly the capabilities and identity declared by ``ActNode``."""

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    resolve_port: ResolvePort
    authorize_port: AuthorizePort
    execute_port: ExecutePort
    settlement_port: SettlementPort
    exchange_writer: ToolExchangeWriter
    failure_reason: OpaqueGraphFailureReason
    admission: ActPayloadAdmission[ActStateT, ActHookCommandT]
    hook_slot: HookSlotId

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.definition_id):
            raise ConfigContractError("ActConfig.definition_id must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ConfigContractError("ActConfig.definition_version must be positive")
        if type(self.admission) is not ActPayloadAdmission:
            raise ConfigContractError("ActConfig.admission must be an ActPayloadAdmission")
        if type(self.hook_slot) is not HookSlotId:
            raise ConfigContractError("ActConfig.hook_slot must be a HookSlotId")
        if (
            self.hook_slot.definition_id != self.definition_id
            or self.hook_slot.definition_version != self.definition_version
        ):
            raise ConfigContractError("ActConfig.hook_slot does not match the Act definition")


def _act_aggregate(config: Config, /) -> ActConfig[HookStateProjection, ActHookCommand]:
    return cast(
        ActConfig[HookStateProjection, ActHookCommand],
        require_config_projection(config.act, ActConfig, "Config.act"),
    )


@dataclass(frozen=True, slots=True)
class ActNodeConfig(ConfigSlice, Generic[ActCapabilityT, ActStateT, ActHookCommandT]):
    """One Act node's capability plus the shared nominal contract."""

    capability: ActCapabilityT
    admission: ActPayloadAdmission[ActStateT, ActHookCommandT]

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if self.capability is None:
            raise ConfigContractError("Act node projection requires its declared capability")
        if type(self.admission) is not ActPayloadAdmission:
            raise ConfigContractError("Act node projection requires an ActPayloadAdmission")


@dataclass(frozen=True, slots=True)
class AuthorizeCapabilities:
    port: AuthorizePort
    failure_reason: OpaqueGraphFailureReason


@dataclass(frozen=True, slots=True)
class SettleCapabilities:
    settlement_port: SettlementPort
    exchange_writer: ToolExchangeWriter


@dataclass(frozen=True, slots=True)
class ResolveBinding(Generic[ActStateT, ActHookCommandT]):
    """Resolve node's narrow Config declaration."""

    def select(self, config: Config, /) -> ActNodeConfig[ResolvePort, ActStateT, ActHookCommandT]:
        selected = _act_aggregate(config)
        return ActNodeConfig(
            selected.snapshot_key,
            selected.resolve_port,
            cast(ActPayloadAdmission[ActStateT, ActHookCommandT], selected.admission),
        )


@dataclass(frozen=True, slots=True)
class AuthorizeBinding(Generic[ActStateT, ActHookCommandT]):
    """Authorize node's narrow Config declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ActNodeConfig[AuthorizeCapabilities, ActStateT, ActHookCommandT]:
        selected = _act_aggregate(config)
        return ActNodeConfig(
            selected.snapshot_key,
            AuthorizeCapabilities(selected.authorize_port, selected.failure_reason),
            cast(ActPayloadAdmission[ActStateT, ActHookCommandT], selected.admission),
        )


@dataclass(frozen=True, slots=True)
class ExecuteBinding(Generic[ActStateT, ActHookCommandT]):
    """Execute node's narrow Config declaration."""

    def select(self, config: Config, /) -> ActNodeConfig[ExecutePort, ActStateT, ActHookCommandT]:
        selected = _act_aggregate(config)
        return ActNodeConfig(
            selected.snapshot_key,
            selected.execute_port,
            cast(ActPayloadAdmission[ActStateT, ActHookCommandT], selected.admission),
        )


@dataclass(frozen=True, slots=True)
class SettleBinding(Generic[ActStateT, ActHookCommandT]):
    """Settle node's narrow Config declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ActNodeConfig[SettleCapabilities, ActStateT, ActHookCommandT]:
        selected = _act_aggregate(config)
        return ActNodeConfig(
            selected.snapshot_key,
            SettleCapabilities(selected.settlement_port, selected.exchange_writer),
            cast(ActPayloadAdmission[ActStateT, ActHookCommandT], selected.admission),
        )


@dataclass(frozen=True, slots=True)
class ActBinding(Generic[ActStateT, ActHookCommandT]):
    """Selector owned by ``ActNode``."""

    def select(self, config: Config, /) -> ActConfig[ActStateT, ActHookCommandT]:
        return cast(
            ActConfig[ActStateT, ActHookCommandT],
            _act_aggregate(config),
        )


__all__ = ["ActBinding", "ActConfig"]

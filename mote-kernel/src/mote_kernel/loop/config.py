"""ReAct-owned narrow configuration projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.contract import ActHookCommand
from mote_kernel.act.contract import HookStateProjection as ActHookStateProjection
from mote_kernel.config import Config, ConfigContractError, ConfigSlice, require_config_projection
from mote_kernel.hooks.contract import HookGraphValue
from mote_kernel.loop.contract import (
    ActToObserveProjector,
    ObserveRoutePolicy,
    ObserveToActProjector,
    ObserveToThinkProjector,
    ThinkToObserveProjector,
)
from mote_kernel.observe.contract import (
    HookStateProjection as ObserveHookStateProjection,
)
from mote_kernel.observe.contract import (
    ObserveHookCommand,
)
from mote_kernel.state.graph_state.identity import GraphDefinitionId, GraphDefinitionVersion, is_canonical_identity

ObserveStateT = TypeVar("ObserveStateT", bound=ObserveHookStateProjection)
ObserveCommandT = TypeVar("ObserveCommandT", bound=ObserveHookCommand)
ThinkPayloadT = TypeVar("ThinkPayloadT")
ThinkStateT = TypeVar("ThinkStateT", bound=HookGraphValue)
ThinkCommandT = TypeVar("ThinkCommandT", bound=HookGraphValue)
ActStateT = TypeVar("ActStateT", bound=ActHookStateProjection)
ActCommandT = TypeVar("ActCommandT", bound=ActHookCommand)
ReActCapabilityT = TypeVar("ReActCapabilityT")


@dataclass(frozen=True, slots=True)
class ReActConfig(
    ConfigSlice,
    Generic[
        ObserveStateT,
        ObserveCommandT,
        ThinkPayloadT,
        ThinkStateT,
        ThinkCommandT,
        ActStateT,
        ActCommandT,
    ],
):
    """Topology and pure hand-off functions declared by ``ReActNode``."""

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    route_policy: ObserveRoutePolicy
    observe_to_act: ObserveToActProjector[ObserveCommandT]
    observe_to_think: ObserveToThinkProjector[ObserveCommandT, ThinkPayloadT, ThinkStateT]
    think_to_observe: ThinkToObserveProjector[ThinkStateT, ThinkCommandT, ObserveStateT]
    act_to_observe: ActToObserveProjector[ActCommandT, ObserveStateT]

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.definition_id):
            raise ConfigContractError("ReActConfig.definition_id must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ConfigContractError("ReActConfig.definition_version must be positive")
        if not callable(self.route_policy):
            raise ConfigContractError("ReActConfig.route_policy must be callable")
        if not callable(self.observe_to_act):
            raise ConfigContractError("ReActConfig.observe_to_act must be callable")
        if not callable(self.observe_to_think):
            raise ConfigContractError("ReActConfig.observe_to_think must be callable")
        if not callable(self.think_to_observe):
            raise ConfigContractError("ReActConfig.think_to_observe must be callable")
        if not callable(self.act_to_observe):
            raise ConfigContractError("ReActConfig.act_to_observe must be callable")


def _react_aggregate(
    config: Config,
    /,
) -> ReActConfig[
    ObserveHookStateProjection,
    ObserveHookCommand,
    object,
    HookGraphValue,
    HookGraphValue,
    ActHookStateProjection,
    ActHookCommand,
]:
    selected = cast(
        ReActConfig[
            ObserveHookStateProjection,
            ObserveHookCommand,
            object,
            HookGraphValue,
            HookGraphValue,
            ActHookStateProjection,
            ActHookCommand,
        ],
        require_config_projection(config.react, ReActConfig, "Config.react"),
    )
    if (
        selected.definition_id != config.snapshot.key.definition_id
        or selected.definition_version != config.snapshot.key.definition_version
    ):
        raise ConfigContractError("ReActConfig does not identify the complete config snapshot")
    return selected


@dataclass(frozen=True, slots=True)
class ReActRuntimeConfig(ConfigSlice):
    """Identity shared by every activation-local ReAct projection."""

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.definition_id):
            raise ConfigContractError("ReAct runtime definition_id must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ConfigContractError("ReAct runtime definition_version must be positive")


@dataclass(frozen=True, slots=True)
class ReActNodeConfig(ReActRuntimeConfig, Generic[ReActCapabilityT]):
    """One ReAct node's single pure routing/projection capability."""

    capability: ReActCapabilityT

    def __post_init__(self) -> None:
        ReActRuntimeConfig.__post_init__(self)
        if not callable(self.capability):
            raise ConfigContractError("ReAct node projection requires one callable capability")


def _runtime_identity(config: Config, /) -> ReActRuntimeConfig:
    selected = _react_aggregate(config)
    return ReActRuntimeConfig(
        selected.snapshot_key,
        selected.definition_id,
        selected.definition_version,
    )


def _runtime_capability(
    config: Config,
    capability: ReActCapabilityT,
    /,
) -> ReActNodeConfig[ReActCapabilityT]:
    selected = _react_aggregate(config)
    return ReActNodeConfig(
        selected.snapshot_key,
        selected.definition_id,
        selected.definition_version,
        capability,
    )


@dataclass(frozen=True, slots=True)
class ReActPrepareBinding:
    """Prepare node's topology-identity declaration."""

    def select(self, config: Config, /) -> ReActRuntimeConfig:
        return _runtime_identity(config)


@dataclass(frozen=True, slots=True)
class ReActRouteBinding:
    """Observe completion router's one-policy declaration."""

    def select(self, config: Config, /) -> ReActNodeConfig[ObserveRoutePolicy]:
        selected = _react_aggregate(config)
        return _runtime_capability(config, selected.route_policy)


@dataclass(frozen=True, slots=True)
class ObserveToActBinding(Generic[ObserveCommandT]):
    """Observe-to-Act node's one-projector declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ReActNodeConfig[ObserveToActProjector[ObserveCommandT]]:
        selected = _react_aggregate(config)
        return _runtime_capability(
            config,
            cast(ObserveToActProjector[ObserveCommandT], selected.observe_to_act),
        )


@dataclass(frozen=True, slots=True)
class ObserveToThinkBinding(Generic[ObserveCommandT, ThinkPayloadT, ThinkStateT]):
    """Observe-to-Think node's one-projector declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ReActNodeConfig[ObserveToThinkProjector[ObserveCommandT, ThinkPayloadT, ThinkStateT]]:
        selected = _react_aggregate(config)
        return _runtime_capability(
            config,
            cast(
                ObserveToThinkProjector[ObserveCommandT, ThinkPayloadT, ThinkStateT],
                selected.observe_to_think,
            ),
        )


@dataclass(frozen=True, slots=True)
class ThinkToObserveBinding(Generic[ThinkStateT, ThinkCommandT, ObserveStateT]):
    """Think-to-Observe node's one-projector declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ReActNodeConfig[ThinkToObserveProjector[ThinkStateT, ThinkCommandT, ObserveStateT]]:
        selected = _react_aggregate(config)
        return _runtime_capability(
            config,
            cast(
                ThinkToObserveProjector[ThinkStateT, ThinkCommandT, ObserveStateT],
                selected.think_to_observe,
            ),
        )


@dataclass(frozen=True, slots=True)
class ActToObserveBinding(Generic[ActCommandT, ObserveStateT]):
    """Act-to-Observe node's one-projector declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ReActNodeConfig[ActToObserveProjector[ActCommandT, ObserveStateT]]:
        selected = _react_aggregate(config)
        return _runtime_capability(
            config,
            cast(
                ActToObserveProjector[ActCommandT, ObserveStateT],
                selected.act_to_observe,
            ),
        )


@dataclass(frozen=True, slots=True)
class ReActBinding(
    Generic[
        ObserveStateT,
        ObserveCommandT,
        ThinkPayloadT,
        ThinkStateT,
        ThinkCommandT,
        ActStateT,
        ActCommandT,
    ]
):
    """Selector owned by the top-level ReAct graph."""

    def select(
        self, config: Config, /
    ) -> ReActConfig[
        ObserveStateT,
        ObserveCommandT,
        ThinkPayloadT,
        ThinkStateT,
        ThinkCommandT,
        ActStateT,
        ActCommandT,
    ]:
        return cast(
            ReActConfig[
                ObserveStateT,
                ObserveCommandT,
                ThinkPayloadT,
                ThinkStateT,
                ThinkCommandT,
                ActStateT,
                ActCommandT,
            ],
            _react_aggregate(config),
        )


__all__: list[str] = []

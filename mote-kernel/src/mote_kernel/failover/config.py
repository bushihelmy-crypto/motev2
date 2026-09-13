"""Failover-owned narrow configuration projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Never, TypeVar, cast

from mote_kernel.config import Config, ConfigContractError, ConfigSlice, revalidate_config_slice
from mote_kernel.failover.contract import AttemptPreparation
from mote_kernel.failover.plan import FailoverPlan, FailoverPortId
from mote_kernel.state.graph_state.identity import is_canonical_identity

RequestT = TypeVar("RequestT")
TransformT = TypeVar("TransformT")


def _failover_config_for_port(
    config: Config,
    port_id: FailoverPortId,
    required: bool,
    /,
) -> FailoverConfig[Never, Never] | None:
    matches: list[FailoverConfig[Never, Never]] = []
    for projection in config.failovers:
        if type(projection) is not FailoverConfig:
            raise ConfigContractError("Config.failovers contains a non-FailoverConfig projection")
        candidate = cast(FailoverConfig[Never, Never], projection)
        revalidate_config_slice(candidate, "Config.failovers FailoverConfig")
        if candidate.port_id == port_id:
            matches.append(candidate)
    if len(matches) > 1:
        raise ConfigContractError("Config.bind found duplicate FailoverConfig projections for one Port")
    if not matches:
        if required:
            raise ConfigContractError("Config.bind requires a FailoverConfig for the requested Port")
        return None
    return matches[0]


@dataclass(frozen=True, slots=True)
class FailoverConfig(ConfigSlice, Generic[RequestT, TransformT]):
    """The resolved failover plan for one concrete Port binding."""

    port_id: FailoverPortId
    plan: FailoverPlan[TransformT]
    preparation: AttemptPreparation[RequestT, TransformT]

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.port_id):
            raise ConfigContractError("FailoverConfig.port_id must be canonical")
        if type(self.plan) is not FailoverPlan:
            raise ConfigContractError("FailoverConfig.plan must be a FailoverPlan")
        try:
            FailoverPlan(self.plan.plan_revision, self.plan.port_id, self.plan.profile)
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
            raise ConfigContractError("FailoverConfig.plan is malformed") from error
        if self.plan.port_id != self.port_id:
            raise ConfigContractError("FailoverConfig.plan does not match its Port binding")
        try:
            prepare_next = self.preparation.prepare_next
        except AttributeError as error:
            raise ConfigContractError("FailoverConfig.preparation must be an AttemptPreparation capability") from error
        if not callable(prepare_next):
            raise ConfigContractError("FailoverConfig.preparation must be an AttemptPreparation capability")


@dataclass(frozen=True, slots=True)
class FailoverPlanConfig(ConfigSlice, Generic[TransformT]):
    """One failover node's plan-only projection."""

    port_id: FailoverPortId
    plan: FailoverPlan[TransformT]

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.port_id):
            raise ConfigContractError("FailoverPlanConfig.port_id must be canonical")
        if type(self.plan) is not FailoverPlan or self.plan.port_id != self.port_id:
            raise ConfigContractError("FailoverPlanConfig.plan must match its Port binding")


@dataclass(frozen=True, slots=True)
class FailoverPrepareConfig(ConfigSlice, Generic[RequestT, TransformT]):
    """Prepare node's plan plus request-preparation capability."""

    port_id: FailoverPortId
    plan: FailoverPlan[TransformT]
    preparation: AttemptPreparation[RequestT, TransformT]

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.port_id):
            raise ConfigContractError("FailoverPrepareConfig.port_id must be canonical")
        if type(self.plan) is not FailoverPlan or self.plan.port_id != self.port_id:
            raise ConfigContractError("FailoverPrepareConfig.plan must match its Port binding")
        try:
            prepare_next = self.preparation.prepare_next
        except AttributeError as error:
            raise ConfigContractError(
                "FailoverPrepareConfig.preparation must be an AttemptPreparation capability"
            ) from error
        if not callable(prepare_next):
            raise ConfigContractError("FailoverPrepareConfig.preparation must be an AttemptPreparation capability")


def _validate_binding(port_id: FailoverPortId, required: bool, owner: str, /) -> None:
    if not is_canonical_identity(port_id):
        raise ConfigContractError(f"{owner}.port_id must be canonical")
    if type(required) is not bool:
        raise ConfigContractError(f"{owner}.required must be a bool")


@dataclass(frozen=True, slots=True)
class FailoverPlanBinding(Generic[TransformT]):
    """Reusable plan-only declaration for Observe and Invoke nodes."""

    port_id: FailoverPortId
    required: bool = True

    def __post_init__(self) -> None:
        _validate_binding(self.port_id, self.required, "FailoverPlanBinding")

    def select(self, config: Config, /) -> FailoverPlanConfig[TransformT] | None:
        selected = _failover_config_for_port(config, self.port_id, self.required)
        if selected is None:
            return None
        return FailoverPlanConfig(
            selected.snapshot_key,
            selected.port_id,
            cast(FailoverPlan[TransformT], selected.plan),
        )


@dataclass(frozen=True, slots=True)
class FailoverPrepareBinding(Generic[RequestT, TransformT]):
    """Prepare node's plan-and-preparation declaration."""

    port_id: FailoverPortId
    required: bool = True

    def __post_init__(self) -> None:
        _validate_binding(self.port_id, self.required, "FailoverPrepareBinding")

    def select(
        self,
        config: Config,
        /,
    ) -> FailoverPrepareConfig[RequestT, TransformT] | None:
        selected = _failover_config_for_port(config, self.port_id, self.required)
        if selected is None:
            return None
        return FailoverPrepareConfig(
            selected.snapshot_key,
            selected.port_id,
            cast(FailoverPlan[TransformT], selected.plan),
            cast(AttemptPreparation[RequestT, TransformT], selected.preparation),
        )


@dataclass(frozen=True, slots=True)
class FailoverBinding(Generic[RequestT, TransformT]):
    """Selector owned by one Port-level failover assembly site."""

    port_id: FailoverPortId
    required: bool = True

    def __post_init__(self) -> None:
        _validate_binding(self.port_id, self.required, "FailoverBinding")

    def select(
        self,
        config: Config,
        /,
    ) -> FailoverConfig[RequestT, TransformT] | None:
        return cast(
            FailoverConfig[RequestT, TransformT] | None,
            _failover_config_for_port(config, self.port_id, self.required),
        )


__all__ = ["FailoverBinding", "FailoverConfig"]

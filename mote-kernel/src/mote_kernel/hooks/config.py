"""Hook-owned narrow configuration projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.config import Config, ConfigContractError, ConfigSlice, revalidate_config_slice
from mote_kernel.hooks.contract import HookInvocationRequest, HookPayloadAdmission, HookStageResult
from mote_kernel.hooks.identity import HookPriority, HookSlotId
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
from mote_kernel.invocation import Invocation

PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


def _revalidate_slot(slot: HookSlotId, field: str, /) -> HookSlotId:
    """Reconstruct a Hook slot after a persistence/deserialization boundary."""

    try:
        return HookSlotId(slot.definition_id, slot.definition_version, slot.node_id, slot.stage)
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
        raise ConfigContractError(f"{field} is malformed") from error


def _is_invocation(
    value: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT],
        HookStageResult[ValueT, CommandT],
    ]
    | None,
) -> bool:
    """Check a decoded capability without widening the stored Port type."""

    return isinstance(value, Invocation) and callable(value.invoke)


def _hook_config_for_slot(
    config: Config,
    slot: HookSlotId,
    /,
) -> HookConfig[object, object, object, object]:
    matches: list[HookConfig[object, object, object, object]] = []
    for projection in config.hooks:
        if type(projection) is not HookConfig:
            raise ConfigContractError("Config.hooks contains a non-HookConfig projection")
        candidate = cast(HookConfig[object, object, object, object], projection)
        revalidate_config_slice(candidate, "Config.hooks HookConfig")
        if candidate.slot == slot:
            matches.append(candidate)
    if len(matches) != 1:
        raise ConfigContractError("Config.bind could not find exactly one HookConfig for the requested slot")
    return matches[0]


@dataclass(frozen=True, slots=True)
class HookConfig(ConfigSlice, Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """The complete resolved config for one concrete Hook slot."""

    slot: HookSlotId
    plan: HookPlan[PriorityConfigT]
    invocation: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT],
        HookStageResult[ValueT, CommandT],
    ]
    payload_admission: HookPayloadAdmission[PriorityConfigT, ValueT, StateT, CommandT]

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if type(self.slot) is not HookSlotId:
            raise ConfigContractError("HookConfig.slot must be a HookSlotId")
        _revalidate_slot(self.slot, "HookConfig.slot")
        if type(self.plan) is not HookPlan:
            raise ConfigContractError("HookConfig.plan must be a HookPlan")
        try:
            plan = HookPlan(
                HookPriorityPlan(self.plan.p1.config),
                HookPriorityPlan(self.plan.p2.config),
            )
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
            raise ConfigContractError("HookConfig.plan is malformed") from error
        if not _is_invocation(self.invocation):
            raise ConfigContractError("HookConfig.invocation must be an Invocation capability")
        if type(self.payload_admission) is not HookPayloadAdmission:
            raise ConfigContractError("HookConfig.payload_admission must be a HookPayloadAdmission")
        try:
            self.payload_admission.admit_plan(plan)
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
            raise ConfigContractError("HookConfig.plan does not satisfy its payload admission") from error


@dataclass(frozen=True, slots=True)
class HookPriorityConfig(ConfigSlice, Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """Exactly one Hook priority's invocation contract."""

    slot: HookSlotId
    priority_plan: HookPriorityPlan[PriorityConfigT]
    invocation: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT],
        HookStageResult[ValueT, CommandT],
    ]
    payload_admission: HookPayloadAdmission[PriorityConfigT, ValueT, StateT, CommandT]

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if type(self.slot) is not HookSlotId:
            raise ConfigContractError("HookPriorityConfig.slot must be a HookSlotId")
        _revalidate_slot(self.slot, "HookPriorityConfig.slot")
        if type(self.priority_plan) is not HookPriorityPlan:
            raise ConfigContractError("HookPriorityConfig.priority_plan must be a HookPriorityPlan")
        if not _is_invocation(self.invocation):
            raise ConfigContractError("HookPriorityConfig.invocation must be an Invocation capability")
        if type(self.payload_admission) is not HookPayloadAdmission:
            raise ConfigContractError("HookPriorityConfig.payload_admission must be a HookPayloadAdmission")
        try:
            self.payload_admission.admit_plan(HookPlan(self.priority_plan, self.priority_plan))
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
            raise ConfigContractError("HookPriorityConfig priority does not satisfy its payload admission") from error


@dataclass(frozen=True, slots=True)
class HookPriorityBinding(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """Reusable selector for exactly one P1 or P2 activation."""

    slot: HookSlotId
    priority: HookPriority

    def __post_init__(self) -> None:
        if type(self.slot) is not HookSlotId:
            raise ConfigContractError("HookPriorityBinding.slot must be a HookSlotId")
        _revalidate_slot(self.slot, "HookPriorityBinding.slot")
        if type(self.priority) is not HookPriority:
            raise ConfigContractError("HookPriorityBinding.priority must be a HookPriority")

    def select(
        self,
        config: Config,
        /,
    ) -> HookPriorityConfig[PriorityConfigT, ValueT, StateT, CommandT]:
        selected = cast(
            HookConfig[PriorityConfigT, ValueT, StateT, CommandT],
            _hook_config_for_slot(config, self.slot),
        )
        priority_plan = selected.plan.p1 if self.priority is HookPriority.P1 else selected.plan.p2
        return HookPriorityConfig(
            selected.snapshot_key,
            selected.slot,
            priority_plan,
            selected.invocation,
            selected.payload_admission,
        )


@dataclass(frozen=True, slots=True)
class HookBinding(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """Selector owned by one Hook node and keyed by its typed slot identity."""

    slot: HookSlotId

    def __post_init__(self) -> None:
        if type(self.slot) is not HookSlotId:
            raise ConfigContractError("HookBinding.slot must be a HookSlotId")
        _revalidate_slot(self.slot, "HookBinding.slot")

    def select(
        self,
        config: Config,
        /,
    ) -> HookConfig[PriorityConfigT, ValueT, StateT, CommandT]:
        return cast(
            HookConfig[PriorityConfigT, ValueT, StateT, CommandT],
            _hook_config_for_slot(config, self.slot),
        )


__all__ = ["HookBinding", "HookConfig"]

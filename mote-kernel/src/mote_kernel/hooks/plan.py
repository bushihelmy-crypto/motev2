"""Immutable configuration snapshots and dynamic hook plans."""

from dataclasses import dataclass
from typing import Generic, TypeVar

PriorityConfigT = TypeVar("PriorityConfigT")


@dataclass(frozen=True, slots=True)
class HookPriorityPlan(Generic[PriorityConfigT]):
    """Configuration interpreted by the extension Port for one priority node."""

    config: PriorityConfigT


@dataclass(frozen=True, slots=True)
class HookPlan(Generic[PriorityConfigT]):
    """The immutable P1/P2 plan shared by one HookNode invocation."""

    p1: HookPriorityPlan[PriorityConfigT]
    p2: HookPriorityPlan[PriorityConfigT]

    def __post_init__(self) -> None:
        if any(type(plan) is not HookPriorityPlan for plan in (self.p1, self.p2)):
            raise TypeError("hook plan priorities must be HookPriorityPlan values")


__all__ = ["HookPlan", "HookPriorityPlan"]

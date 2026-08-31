"""Typed values and configuration contracts consumed by HookNode."""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan

ConfigT = TypeVar("ConfigT")
PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


class HookContractError(ValueError):
    """Raised when a hook value crosses its typed owner boundary incorrectly."""


class HookGraphValue:
    """Internal nominal base for values carried by the HookNode graph."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class HookRequest(HookGraphValue, Generic[ValueT, StateT]):
    """The current value and read-only state presented to one priority Port."""

    value: ValueT
    state: StateT


@dataclass(frozen=True, slots=True)
class HookResult(HookGraphValue, Generic[ValueT, CommandT]):
    """One priority Port's final value and already-merged typed commands."""

    value: ValueT
    commands: tuple[CommandT, ...] = ()

    def __post_init__(self) -> None:
        if type(self.commands) is not tuple:
            raise TypeError("hook result commands must be a tuple")


class HookConfigSource(Protocol[ConfigT]):
    """Read the current immutable configuration once for a HookNode invocation."""

    def snapshot(self) -> HookConfigSnapshot[ConfigT]: ...


class HookPlanLoader(Protocol[ConfigT, PriorityConfigT]):
    """Build one immutable dynamic plan from the captured configuration."""

    def load(self, snapshot: HookConfigSnapshot[ConfigT], /) -> HookPlan[PriorityConfigT]: ...


__all__ = [
    "HookConfigSource",
    "HookContractError",
    "HookGraphValue",
    "HookPlanLoader",
    "HookRequest",
    "HookResult",
]

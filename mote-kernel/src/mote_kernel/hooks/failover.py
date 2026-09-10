"""Hook-local binding for the single public Failover decorator.

Hooks have one capability boundary: the shared ``Invocation`` used by P1 and
P2.  The failover wrapper therefore belongs around that invocation, before a
``HookPort`` is created.  Wrapping ``HookNode`` or either priority graph would
create a second execution boundary and would also make P1/P2 use different
capabilities after a Config rebinding.

The retry implementation remains solely in :mod:`mote_kernel.failover`; this
module only preserves the Hook invocation protocol while assembling a node.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Self, TypeVar

from mote_kernel.failover.contract import (
    PortDecorator as HookFailoverDecorator,
)
from mote_kernel.failover.contract import (
    TypedPortDecorator,
    apply_port_decorator_boundary,
    require_port_decorator_boundary,
)
from mote_kernel.hooks.contract import (
    HookContractError,
    HookInvocationRequest,
    HookStageResult,
)
from mote_kernel.invocation import Invocation

PriorityConfigT = TypeVar("PriorityConfigT")
ValueT = TypeVar("ValueT")
StateT = TypeVar("StateT")
CommandT = TypeVar("CommandT")


@dataclass(frozen=True, slots=True)
class HookFailoverDecorators(Generic[PriorityConfigT, ValueT, StateT, CommandT]):
    """The one Hook failover declaration shared by P1 and P2."""

    invocation: (
        TypedPortDecorator[
            Invocation[
                HookInvocationRequest[PriorityConfigT, ValueT],
                HookStageResult[ValueT, CommandT],
            ]
        ]
        | HookFailoverDecorator
        | None
    ) = None

    def __post_init__(self) -> None:
        require_port_decorator_boundary(self.invocation, "HookFailoverDecorators.invocation", HookContractError)

    @classmethod
    def disabled(cls) -> Self:
        """Return an explicit no-failover Hook binding."""

        return cls()

    @classmethod
    def uniform(cls, decorator: HookFailoverDecorator, /) -> Self:
        """Build the single shared Hook binding from one decorator."""

        require_port_decorator_boundary(decorator, "HookFailoverDecorators.uniform", HookContractError)
        return cls(decorator)

    def decorate(
        self,
        invocation: Invocation[
            HookInvocationRequest[PriorityConfigT, ValueT],
            HookStageResult[ValueT, CommandT],
        ],
        /,
    ) -> Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT],
        HookStageResult[ValueT, CommandT],
    ]:
        return apply_port_decorator_boundary(
            invocation, self.invocation, Invocation, "Hook Invocation", HookContractError
        )


def normalize_hook_failover_decorators(
    value: HookFailoverDecorators[PriorityConfigT, ValueT, StateT, CommandT] | HookFailoverDecorator | None,
    /,
) -> HookFailoverDecorators[PriorityConfigT, ValueT, StateT, CommandT]:
    """Normalize one shared Hook declaration at the assembly boundary."""

    if value is None:
        return HookFailoverDecorators[PriorityConfigT, ValueT, StateT, CommandT].disabled()
    if type(value) is HookFailoverDecorators:
        return value
    if callable(value):
        return HookFailoverDecorators[PriorityConfigT, ValueT, StateT, CommandT].uniform(value)
    raise HookContractError("Hook failover decoration requires a callable decorator or HookFailoverDecorators")


__all__ = [
    "HookFailoverDecorator",
    "HookFailoverDecorators",
    "normalize_hook_failover_decorators",
]

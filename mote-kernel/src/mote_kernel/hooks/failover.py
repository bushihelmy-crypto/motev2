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
    PortDecoratorContractError,
    TypedPortDecorator,
    apply_port_decorator,
    require_port_decorator,
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
PortT = TypeVar("PortT")


def _validate_decorator(
    value: TypedPortDecorator[PortT] | HookFailoverDecorator | None,
    field: str,
    /,
) -> None:
    try:
        require_port_decorator(value, field)
    except PortDecoratorContractError as error:
        raise HookContractError(str(error)) from error


def _apply(
    invocation: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT],
        HookStageResult[ValueT, CommandT],
    ],
    decorator: TypedPortDecorator[
        Invocation[
            HookInvocationRequest[PriorityConfigT, ValueT],
            HookStageResult[ValueT, CommandT],
        ]
    ]
    | HookFailoverDecorator
    | None,
    field: str,
    /,
) -> Invocation[
    HookInvocationRequest[PriorityConfigT, ValueT],
    HookStageResult[ValueT, CommandT],
]:
    try:
        return apply_port_decorator(invocation, decorator, Invocation, field)
    except PortDecoratorContractError as error:
        raise HookContractError(str(error)) from error


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
        _validate_decorator(self.invocation, "HookFailoverDecorators.invocation")

    @classmethod
    def disabled(cls) -> Self:
        """Return an explicit no-failover Hook binding."""

        return cls()

    @classmethod
    def uniform(cls, decorator: HookFailoverDecorator, /) -> Self:
        """Build the single shared Hook binding from one decorator."""

        _validate_decorator(decorator, "HookFailoverDecorators.uniform")
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
        return _apply(invocation, self.invocation, "Hook Invocation")


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


def decorate_hook_invocation(
    invocation: Invocation[
        HookInvocationRequest[PriorityConfigT, ValueT],
        HookStageResult[ValueT, CommandT],
    ],
    /,
    *,
    failover: HookFailoverDecorators[PriorityConfigT, ValueT, StateT, CommandT] | HookFailoverDecorator | None = None,
) -> Invocation[
    HookInvocationRequest[PriorityConfigT, ValueT],
    HookStageResult[ValueT, CommandT],
]:
    """Apply the shared public Failover decorator to one Hook Invocation."""

    return normalize_hook_failover_decorators(failover).decorate(invocation)


__all__ = [
    "HookFailoverDecorator",
    "HookFailoverDecorators",
    "decorate_hook_invocation",
    "normalize_hook_failover_decorators",
]

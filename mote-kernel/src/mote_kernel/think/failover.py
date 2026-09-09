"""Think-local assembly seam for typed Port failover decoration.

Think does not own retry policy or a failover runner.  The composition root
supplies a decorator that keeps each Port's existing protocol surface; this
module applies that decorator to the six Think capabilities.  ``PromptPort``
is deliberately treated as one object, so its three ordered methods cannot be
split or accidentally lose one of the synchronous/async boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Self, TypeVar

from mote_kernel.failover.contract import PortDecorator as FailoverPortDecorator
from mote_kernel.failover.contract import (
    TypedPortDecorator,
    apply_port_decorator_boundary,
    require_port_decorator_boundary,
)
from mote_kernel.hooks.contract import HookGraphValue
from mote_kernel.think.contract import (
    CommandPort,
    CompactedContext,
    CompactPort,
    CompactRequest,
    ContextFrame,
    ContextPort,
    ContextRequest,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    PromptPort,
    RouterPort,
    RouterRequest,
    ThinkContractError,
    ThinkCoreResult,
)

PortT = TypeVar("PortT")

PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")
CommandT = TypeVar("CommandT")


def apply_think_port_decorator(
    port: PortT,
    decorator: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    expected: type[PortT],
    field: str,
    /,
) -> PortT:
    """Apply one stage's typed decorator without requiring a full bundle."""

    return apply_port_decorator_boundary(port, decorator, expected, field, ThinkContractError)


@dataclass(frozen=True, slots=True)
class ThinkFailoverDecorators(
    Generic[
        PayloadT,
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
        ModelOutputT,
        CommandT,
    ]
):
    """Per-Port failover declarations for Think.

    ``None`` is an explicit disabled binding.  The individual methods are
    reused for activation-time Config rebinding, preventing a successor Config
    from bypassing the assembly decision.
    """

    prompt: (
        TypedPortDecorator[PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]]
        | FailoverPortDecorator
        | None
    ) = None
    context: (
        TypedPortDecorator[
            ContextPort[
                ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
                ContextFrame[ContextSnapshotT],
            ]
        ]
        | FailoverPortDecorator
        | None
    ) = None
    compact: (
        TypedPortDecorator[
            CompactPort[
                CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
                CompactedContext[CompactedSnapshotT],
            ]
        ]
        | FailoverPortDecorator
        | None
    ) = None
    router: (
        TypedPortDecorator[RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]]
        | FailoverPortDecorator
        | None
    ) = None
    inference: (
        TypedPortDecorator[
            InferencePort[
                InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
                InferenceResult[ModelOutputT],
            ]
        ]
        | FailoverPortDecorator
        | None
    ) = None
    command: (
        TypedPortDecorator[CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]]]
        | FailoverPortDecorator
        | None
    ) = None

    def __post_init__(self) -> None:
        require_port_decorator_boundary(self.prompt, "ThinkFailoverDecorators.prompt", ThinkContractError)
        require_port_decorator_boundary(self.context, "ThinkFailoverDecorators.context", ThinkContractError)
        require_port_decorator_boundary(self.compact, "ThinkFailoverDecorators.compact", ThinkContractError)
        require_port_decorator_boundary(self.router, "ThinkFailoverDecorators.router", ThinkContractError)
        require_port_decorator_boundary(self.inference, "ThinkFailoverDecorators.inference", ThinkContractError)
        require_port_decorator_boundary(self.command, "ThinkFailoverDecorators.command", ThinkContractError)

    @classmethod
    def disabled(cls) -> Self:
        """Return explicit no-failover bindings for all Think Ports."""

        return cls()

    @classmethod
    def uniform(cls, decorator: FailoverPortDecorator, /) -> Self:
        """Apply one typed-preserving decorator to all six Think Ports."""

        require_port_decorator_boundary(decorator, "ThinkFailoverDecorators.uniform", ThinkContractError)
        return cls(decorator, decorator, decorator, decorator, decorator, decorator)

    def prompt_port(
        self,
        port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT],
        /,
    ) -> PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]:
        return apply_port_decorator_boundary(port, self.prompt, PromptPort, "PromptPort", ThinkContractError)

    def context_port(
        self,
        port: ContextPort[
            ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
            ContextFrame[ContextSnapshotT],
        ],
        /,
    ) -> ContextPort[
        ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
        ContextFrame[ContextSnapshotT],
    ]:
        return apply_port_decorator_boundary(port, self.context, ContextPort, "ContextPort", ThinkContractError)

    def compact_port(
        self,
        port: CompactPort[
            CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
            CompactedContext[CompactedSnapshotT],
        ],
        /,
    ) -> CompactPort[
        CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
        CompactedContext[CompactedSnapshotT],
    ]:
        return apply_port_decorator_boundary(port, self.compact, CompactPort, "CompactPort", ThinkContractError)

    def router_port(
        self,
        port: RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]],
        /,
    ) -> RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]:
        return apply_port_decorator_boundary(port, self.router, RouterPort, "RouterPort", ThinkContractError)

    def inference_port(
        self,
        port: InferencePort[
            InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
            InferenceResult[ModelOutputT],
        ],
        /,
    ) -> InferencePort[
        InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
        InferenceResult[ModelOutputT],
    ]:
        return apply_port_decorator_boundary(port, self.inference, InferencePort, "InferencePort", ThinkContractError)

    def command_port(
        self,
        port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]],
        /,
    ) -> CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]]:
        return apply_port_decorator_boundary(port, self.command, CommandPort, "CommandPort", ThinkContractError)


def normalize_think_failover_decorators(
    value: ThinkFailoverDecorators[
        PayloadT,
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
        ModelOutputT,
        CommandT,
    ]
    | FailoverPortDecorator
    | None,
    /,
) -> ThinkFailoverDecorators[
    PayloadT,
    HookStateT,
    SystemPromptT,
    PlaceholderT,
    UserPromptT,
    ContextSnapshotT,
    CompactedSnapshotT,
    ModelOutputT,
    CommandT,
]:
    """Normalize a per-Port bundle or one uniform composition decorator."""

    if value is None:
        return ThinkFailoverDecorators[
            PayloadT,
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
            ModelOutputT,
            CommandT,
        ].disabled()
    if type(value) is ThinkFailoverDecorators:
        return value
    if callable(value):
        return ThinkFailoverDecorators[
            PayloadT,
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
            ModelOutputT,
            CommandT,
        ].uniform(value)
    raise ThinkContractError("Think failover decoration requires a callable decorator or ThinkFailoverDecorators")


__all__ = [
    "FailoverPortDecorator",
    "ThinkFailoverDecorators",
    "apply_think_port_decorator",
    "normalize_think_failover_decorators",
]

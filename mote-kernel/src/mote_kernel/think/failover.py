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
    PortDecoratorContractError,
    TypedPortDecorator,
    apply_port_decorator,
    require_port_decorator,
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


def _validate_decorator(
    value: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    field: str,
    /,
) -> None:
    try:
        require_port_decorator(value, field)
    except PortDecoratorContractError as error:
        raise ThinkContractError(str(error)) from error


def _apply(
    port: PortT,
    decorator: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    expected: type[PortT],
    field: str,
    /,
) -> PortT:
    try:
        return apply_port_decorator(port, decorator, expected, field)
    except PortDecoratorContractError as error:
        raise ThinkContractError(str(error)) from error


def apply_think_port_decorator(
    port: PortT,
    decorator: TypedPortDecorator[PortT] | FailoverPortDecorator | None,
    expected: type[PortT],
    field: str,
    /,
) -> PortT:
    """Apply one stage's typed decorator without requiring a full bundle."""

    return _apply(port, decorator, expected, field)


def _require_port(port: PortT, expected: type[PortT], field: str, /) -> PortT:
    if not isinstance(port, expected):
        raise ThinkContractError(f"{field} requires its declared Port contract")
    return port


@dataclass(frozen=True, slots=True)
class ThinkPortSet(
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
    """The six typed capabilities consumed by ``ThinkNode``."""

    prompt_port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]
    context_port: ContextPort[
        ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
        ContextFrame[ContextSnapshotT],
    ]
    compact_port: CompactPort[
        CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
        CompactedContext[CompactedSnapshotT],
    ]
    router_port: RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]
    inference_port: InferencePort[
        InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
        InferenceResult[ModelOutputT],
    ]
    command_port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]]

    def __post_init__(self) -> None:
        _require_port(
            self.prompt_port,
            PromptPort,
            "PromptPort",
        )
        if not callable(self.prompt_port.load_system_prompt):
            raise ThinkContractError("PromptPort.load_system_prompt must be callable")
        if not callable(self.prompt_port.load_placeholder):
            raise ThinkContractError("PromptPort.load_placeholder must be callable")
        if not callable(self.prompt_port.load_user_prompt):
            raise ThinkContractError("PromptPort.load_user_prompt must be callable")
        _require_port(self.context_port, ContextPort, "ContextPort")
        if not callable(self.context_port.load_context):
            raise ThinkContractError("ContextPort.load_context must be callable")
        _require_port(self.compact_port, CompactPort, "CompactPort")
        if not callable(self.compact_port.compact):
            raise ThinkContractError("CompactPort.compact must be callable")
        _require_port(self.router_port, RouterPort, "RouterPort")
        if not callable(self.router_port.route_model):
            raise ThinkContractError("RouterPort.route_model must be callable")
        _require_port(self.inference_port, InferencePort, "InferencePort")
        if not callable(self.inference_port.infer):
            raise ThinkContractError("InferencePort.infer must be callable")
        _require_port(self.command_port, CommandPort, "CommandPort")
        if not callable(self.command_port.build_command):
            raise ThinkContractError("CommandPort.build_command must be callable")


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
        _validate_decorator(self.prompt, "ThinkFailoverDecorators.prompt")
        _validate_decorator(self.context, "ThinkFailoverDecorators.context")
        _validate_decorator(self.compact, "ThinkFailoverDecorators.compact")
        _validate_decorator(self.router, "ThinkFailoverDecorators.router")
        _validate_decorator(self.inference, "ThinkFailoverDecorators.inference")
        _validate_decorator(self.command, "ThinkFailoverDecorators.command")

    @classmethod
    def disabled(cls) -> Self:
        """Return explicit no-failover bindings for all Think Ports."""

        return cls()

    @classmethod
    def uniform(cls, decorator: FailoverPortDecorator, /) -> Self:
        """Apply one typed-preserving decorator to all six Think Ports."""

        _validate_decorator(decorator, "ThinkFailoverDecorators.uniform")
        return cls(decorator, decorator, decorator, decorator, decorator, decorator)

    def prompt_port(
        self,
        port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT],
        /,
    ) -> PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]:
        return _apply(port, self.prompt, PromptPort, "PromptPort")

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
        return _apply(port, self.context, ContextPort, "ContextPort")

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
        return _apply(port, self.compact, CompactPort, "CompactPort")

    def router_port(
        self,
        port: RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]],
        /,
    ) -> RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]:
        return _apply(port, self.router, RouterPort, "RouterPort")

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
        return _apply(port, self.inference, InferencePort, "InferencePort")

    def command_port(
        self,
        port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]],
        /,
    ) -> CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]]:
        return _apply(port, self.command, CommandPort, "CommandPort")

    def decorate(
        self,
        ports: ThinkPortSet[
            PayloadT,
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
            ModelOutputT,
            CommandT,
        ],
        /,
    ) -> ThinkPortSet[
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
        if type(ports) is not ThinkPortSet:
            raise ThinkContractError("Think failover decoration requires a ThinkPortSet")
        prompt_port = self.prompt_port(ports.prompt_port)
        context_port = self.context_port(ports.context_port)
        compact_port = self.compact_port(ports.compact_port)
        router_port = self.router_port(ports.router_port)
        inference_port = self.inference_port(ports.inference_port)
        command_port = self.command_port(ports.command_port)
        if (
            prompt_port is ports.prompt_port
            and context_port is ports.context_port
            and compact_port is ports.compact_port
            and router_port is ports.router_port
            and inference_port is ports.inference_port
            and command_port is ports.command_port
        ):
            return ports
        return ThinkPortSet(
            prompt_port,
            context_port,
            compact_port,
            router_port,
            inference_port,
            command_port,
        )


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


def decorate_think_ports(
    prompt_port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT],
    context_port: ContextPort[
        ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
        ContextFrame[ContextSnapshotT],
    ],
    compact_port: CompactPort[
        CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
        CompactedContext[CompactedSnapshotT],
    ],
    router_port: RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]],
    inference_port: InferencePort[
        InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
        InferenceResult[ModelOutputT],
    ],
    command_port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]],
    /,
    *,
    failover: ThinkFailoverDecorators[
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
    | None = None,
) -> ThinkPortSet[
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
    """Apply Think's failover declarations before ``ThinkNode`` assembly."""

    decorators = normalize_think_failover_decorators(failover)
    # Apply to raw capabilities before constructing the validated set so
    # each assembly path performs one contract check.
    return ThinkPortSet(
        decorators.prompt_port(prompt_port),
        decorators.context_port(context_port),
        decorators.compact_port(compact_port),
        decorators.router_port(router_port),
        decorators.inference_port(inference_port),
        decorators.command_port(command_port),
    )


__all__ = [
    "FailoverPortDecorator",
    "ThinkFailoverDecorators",
    "ThinkPortSet",
    "apply_think_port_decorator",
    "decorate_think_ports",
    "normalize_think_failover_decorators",
]

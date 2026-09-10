"""Think-owned narrow configuration projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar, cast

from mote_kernel.config import Config, ConfigContractError, ConfigSlice, require_config_projection
from mote_kernel.hooks.contract import HookGraphValue
from mote_kernel.hooks.identity import HookSlotId
from mote_kernel.state.graph_state.identity import GraphDefinitionId, GraphDefinitionVersion, is_canonical_identity
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
    ThinkCoreResult,
)

PriorityConfigT = TypeVar("PriorityConfigT")
PayloadT = TypeVar("PayloadT")
ThinkStateT = TypeVar("ThinkStateT", bound=HookGraphValue)
ThinkHookCommandT = TypeVar("ThinkHookCommandT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")
ThinkCommandT = TypeVar("ThinkCommandT")
PortT = TypeVar("PortT")


@dataclass(frozen=True, slots=True)
class ThinkConfig(
    ConfigSlice,
    Generic[
        PriorityConfigT,
        PayloadT,
        ThinkStateT,
        ThinkHookCommandT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
        ModelOutputT,
        ThinkCommandT,
    ],
):
    """Exactly the capabilities and identity declared by ``ThinkNode``."""

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    prompt_port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]
    context_port: ContextPort[
        ContextRequest[PayloadT, ThinkStateT, SystemPromptT, PlaceholderT, UserPromptT],
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
    command_port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[ThinkCommandT]]
    hook_state_type: type[ThinkStateT]
    hook_slot: HookSlotId

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if not is_canonical_identity(self.definition_id):
            raise ConfigContractError("ThinkConfig.definition_id must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ConfigContractError("ThinkConfig.definition_version must be positive")
        if type(self.hook_slot) is not HookSlotId:
            raise ConfigContractError("ThinkConfig.hook_slot must be a HookSlotId")
        if (
            self.hook_slot.definition_id != self.definition_id
            or self.hook_slot.definition_version != self.definition_version
        ):
            raise ConfigContractError("ThinkConfig.hook_slot does not match the Think definition")


_ThinkAggregate: TypeAlias = ThinkConfig[
    object,
    object,
    HookGraphValue,
    HookGraphValue,
    object,
    object,
    object,
    object,
    object,
    object,
    object,
]


def _think_aggregate(config: Config, /) -> _ThinkAggregate:
    """Admit the stored Think aggregate once for all node selectors."""

    return cast(
        _ThinkAggregate,
        require_config_projection(config.think, ThinkConfig, "Config.think"),
    )


@dataclass(frozen=True, slots=True)
class ThinkPortConfig(ConfigSlice, Generic[PortT]):
    """One activation-local projection containing exactly one Think Port."""

    port: PortT

    def __post_init__(self) -> None:
        ConfigSlice.__post_init__(self)
        if self.port is None:
            raise ConfigContractError("Think Port projection requires its declared Port")


@dataclass(frozen=True, slots=True)
class PromptBinding(Generic[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]):
    """Prompt node's one-Port declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ThinkPortConfig[PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]]:
        selected = _think_aggregate(config)
        return ThinkPortConfig(
            selected.snapshot_key,
            cast(PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT], selected.prompt_port),
        )


@dataclass(frozen=True, slots=True)
class ContextBinding(Generic[PayloadT, ThinkStateT, SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT]):
    """Context node's one-Port declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ThinkPortConfig[
        ContextPort[
            ContextRequest[PayloadT, ThinkStateT, SystemPromptT, PlaceholderT, UserPromptT],
            ContextFrame[ContextSnapshotT],
        ]
    ]:
        selected = _think_aggregate(config)
        return ThinkPortConfig(
            selected.snapshot_key,
            cast(
                ContextPort[
                    ContextRequest[PayloadT, ThinkStateT, SystemPromptT, PlaceholderT, UserPromptT],
                    ContextFrame[ContextSnapshotT],
                ],
                selected.context_port,
            ),
        )


@dataclass(frozen=True, slots=True)
class CompactBinding(Generic[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT, CompactedSnapshotT]):
    """Compact node's one-Port declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ThinkPortConfig[
        CompactPort[
            CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
            CompactedContext[CompactedSnapshotT],
        ]
    ]:
        selected = _think_aggregate(config)
        return ThinkPortConfig(
            selected.snapshot_key,
            cast(
                CompactPort[
                    CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
                    CompactedContext[CompactedSnapshotT],
                ],
                selected.compact_port,
            ),
        )


@dataclass(frozen=True, slots=True)
class RouterBinding(Generic[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]):
    """Router node's one-Port declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ThinkPortConfig[RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]]:
        selected = _think_aggregate(config)
        return ThinkPortConfig(
            selected.snapshot_key,
            cast(
                RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]],
                selected.router_port,
            ),
        )


@dataclass(frozen=True, slots=True)
class InferenceBinding(Generic[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT]):
    """Inference node's one-Port declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ThinkPortConfig[
        InferencePort[
            InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
            InferenceResult[ModelOutputT],
        ]
    ]:
        selected = _think_aggregate(config)
        return ThinkPortConfig(
            selected.snapshot_key,
            cast(
                InferencePort[
                    InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
                    InferenceResult[ModelOutputT],
                ],
                selected.inference_port,
            ),
        )


@dataclass(frozen=True, slots=True)
class CommandBinding(Generic[ModelOutputT, ThinkCommandT]):
    """Command node's one-Port declaration."""

    def select(
        self,
        config: Config,
        /,
    ) -> ThinkPortConfig[CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[ThinkCommandT]]]:
        selected = _think_aggregate(config)
        return ThinkPortConfig(
            selected.snapshot_key,
            cast(
                CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[ThinkCommandT]],
                selected.command_port,
            ),
        )


@dataclass(frozen=True, slots=True)
class ThinkBinding(
    Generic[
        PriorityConfigT,
        PayloadT,
        ThinkStateT,
        ThinkHookCommandT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
        ModelOutputT,
        ThinkCommandT,
    ]
):
    """Selector owned by ``ThinkNode``."""

    def select(
        self, config: Config, /
    ) -> ThinkConfig[
        PriorityConfigT,
        PayloadT,
        ThinkStateT,
        ThinkHookCommandT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
        ModelOutputT,
        ThinkCommandT,
    ]:
        return cast(
            ThinkConfig[
                PriorityConfigT,
                PayloadT,
                ThinkStateT,
                ThinkHookCommandT,
                SystemPromptT,
                PlaceholderT,
                UserPromptT,
                ContextSnapshotT,
                CompactedSnapshotT,
                ModelOutputT,
                ThinkCommandT,
            ],
            _think_aggregate(config),
        )


__all__ = ["ThinkBinding", "ThinkConfig"]

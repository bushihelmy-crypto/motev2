"""Model-routing stage callable for the Think graph."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.failover.contract import TypedPortDecorator
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import RouterBinding
from mote_kernel.think.contract import (
    CompactStep,
    ModelBinding,
    RouterPort,
    RouterRequest,
    RouterStep,
    ThinkContractError,
    ThinkFrame,
    admit_compact_frame,
)
from mote_kernel.think.failover import (
    FailoverPortDecorator,
    apply_think_port_decorator,
)
from mote_kernel.think.identity import ThinkNodeId

HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
HookCommandT = TypeVar("HookCommandT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")


@dataclass(frozen=True, slots=True)
class RouterNode(
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        ContextSnapshotT,
        CompactedSnapshotT,
    ],
):
    """Select the model binding immediately before model inference."""

    router_port: RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]
    failover: (
        TypedPortDecorator[RouterPort[RouterRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT]]]
        | FailoverPortDecorator
        | None
    ) = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        port = apply_think_port_decorator(self.router_port, self.failover, RouterPort, "RouterPort")
        object.__setattr__(self, "router_port", port)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ThinkContractError("router assembly snapshot key is malformed")
        if operator.is_(port, None):
            raise ThinkContractError("router requires a RouterPort")
        try:
            method = port.route_model
        except AttributeError as error:
            raise ThinkContractError("router requires a RouterPort") from error
        if not callable(method):
            raise ThinkContractError("RouterPort.route_model must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            HookResult[
                ThinkFrame[
                    CompactStep[
                        SystemPromptT,
                        PlaceholderT,
                        UserPromptT,
                        ContextSnapshotT,
                        CompactedSnapshotT,
                    ],
                    HookStateT,
                ],
                HookCommandT,
            ]
        ],
        /,
    ) -> HookActivationRequest[
        ThinkFrame[
            RouterStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
            HookStateT,
        ],
        HookStateT,
    ]:
        frame = admit_compact_frame(activation.value)
        config = activation.activation_config
        step = frame.step
        request = RouterRequest(step.prompt, step.compacted)

        router_port = self.router_port
        if config is not None:
            selected = config.bind(
                RouterBinding[
                    SystemPromptT,
                    PlaceholderT,
                    UserPromptT,
                    CompactedSnapshotT,
                ]()
            )
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                router_port = apply_think_port_decorator(selected.port, self.failover, RouterPort, "RouterPort")
        model_value = await router_port.route_model(request)
        if type(model_value) is not ModelBinding:
            raise ThinkContractError("RouterPort.route_model must return a ModelBinding")
        model = model_value
        next_frame = ThinkFrame(RouterStep(step.prompt, step.compacted, model), frame.hook_state)
        return HookActivationRequest(
            next_frame,
            frame.hook_state,
            GraphNodeId(str(ThinkNodeId.ROUTER)),
        )


__all__ = ["RouterNode"]

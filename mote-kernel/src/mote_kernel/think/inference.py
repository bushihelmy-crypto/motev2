"""Inference stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from mote_kernel.config import ConfigActivation, ConfigSnapshotKey
from mote_kernel.failover.contract import TypedPortDecorator
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.config import InferenceBinding
from mote_kernel.think.contract import (
    InferencePort,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    RouterStep,
    ThinkContractError,
    ThinkFrame,
    admit_router_frame,
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
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")


@dataclass(frozen=True, slots=True)
class InferenceNode(
    Generic[
        HookStateT,
        SystemPromptT,
        PlaceholderT,
        UserPromptT,
        CompactedSnapshotT,
        ModelOutputT,
    ],
):
    """Assemble the final model request and invoke the injected model Port."""

    inference_port: InferencePort[
        InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
        InferenceResult[ModelOutputT],
    ]
    failover: (
        TypedPortDecorator[
            InferencePort[
                InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
                InferenceResult[ModelOutputT],
            ]
        ]
        | FailoverPortDecorator
        | None
    ) = None
    assembly_snapshot_key: ConfigSnapshotKey | None = field(default=None, kw_only=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        port = apply_think_port_decorator(self.inference_port, self.failover, InferencePort, "InferencePort")
        object.__setattr__(self, "inference_port", port)
        if self.assembly_snapshot_key is not None and type(self.assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ThinkContractError("inference assembly snapshot key is malformed")
        try:
            method = port.infer
        except AttributeError as error:
            raise ThinkContractError("inference requires an InferencePort") from error
        if not callable(method):
            raise ThinkContractError("InferencePort.infer must be callable")

    async def __call__(
        self,
        activation: ConfigActivation[
            HookResult[
                ThinkFrame[
                    RouterStep[
                        SystemPromptT,
                        PlaceholderT,
                        UserPromptT,
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
            InferenceStep[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT, ModelOutputT],
            HookStateT,
        ],
        HookStateT,
    ]:
        frame = admit_router_frame(activation.value)
        config = activation.activation_config
        step = frame.step
        compacted = step.compacted
        prompt = step.prompt
        model = step.model
        request = InferenceRequest(prompt, compacted, model)

        inference_port = self.inference_port
        if config is not None:
            selected = config.bind(
                InferenceBinding[
                    SystemPromptT,
                    PlaceholderT,
                    UserPromptT,
                    CompactedSnapshotT,
                    ModelOutputT,
                ]()
            )
            if self.assembly_snapshot_key is None or selected.snapshot_key != self.assembly_snapshot_key:
                inference_port = apply_think_port_decorator(
                    selected.port,
                    self.failover,
                    InferencePort,
                    "InferencePort",
                )
        result_value = await inference_port.infer(request)
        if type(result_value) is not InferenceResult:
            raise ThinkContractError("InferencePort.infer must return an InferenceResult")
        result = result_value
        next_frame = ThinkFrame(InferenceStep(prompt, compacted, model, result), frame.hook_state)
        return HookActivationRequest(
            next_frame,
            frame.hook_state,
            GraphNodeId(str(ThinkNodeId.INFERENCE)),
        )


__all__ = ["InferenceNode"]

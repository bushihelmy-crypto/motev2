"""Inference stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest, HookResult
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import (
    CompactStep,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    ModelBinding,
    ThinkContractError,
    ThinkFrame,
    ThinkStep,
)

HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")


MethodT = TypeVar("MethodT")


def _require_port_method(method: MethodT, name: str, /) -> MethodT:
    if not callable(method):
        raise ThinkContractError(f"InferencePort.{name} must be callable")
    return method


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

    inference_port: (
        InferencePort[
            InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
            InferenceResult[ModelOutputT],
        ]
        | None
    )
    model_binding: ModelBinding

    def __post_init__(self) -> None:
        if self.inference_port is None:
            raise ThinkContractError("inference requires an InferencePort")
        try:
            method = self.inference_port.infer
        except AttributeError as error:
            raise ThinkContractError("inference requires an InferencePort") from error
        _require_port_method(method, "infer")
        if type(self.model_binding) is not ModelBinding:
            raise ThinkContractError("inference requires an exact ModelBinding")

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        hook_value = values["hook_result"]
        if type(hook_value) is not HookResult:
            raise ThinkContractError("inference input must be a HookResult")
        hook_result = cast(HookResult[ThinkFrame[ThinkStep, HookStateT], HookGraphValue], hook_value)
        frame_value = hook_result.value
        if type(frame_value) is not ThinkFrame:
            raise ThinkContractError("inference HookResult must contain a ThinkFrame")
        frame = frame_value
        raw_step = frame.step
        if type(raw_step) is not CompactStep:
            raise ThinkContractError("inference requires a CompactStep")
        if hook_result.node_id is not None and hook_result.node_id != GraphNodeId("compact"):
            raise ThinkContractError("inference requires a HookResult produced by compact")
        step = cast(
            CompactStep[SystemPromptT, PlaceholderT, UserPromptT, HookGraphValue, CompactedSnapshotT],
            raw_step,
        )
        compacted = step.compacted
        prompt = step.prompt
        request = InferenceRequest(prompt, compacted, self.model_binding)

        port = cast(
            InferencePort[
                InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
                InferenceResult[ModelOutputT],
            ],
            self.inference_port,
        )
        result_value = await port.infer(request)
        if type(result_value) is not InferenceResult:
            raise ThinkContractError("InferencePort.infer must return an InferenceResult")
        result = result_value
        next_frame = ThinkFrame(InferenceStep(prompt, compacted, result), frame.hook_state)
        return Graph.values(hook_request=HookRequest(next_frame, frame.hook_state, GraphNodeId("inference")))


__all__ = ["InferenceNode"]

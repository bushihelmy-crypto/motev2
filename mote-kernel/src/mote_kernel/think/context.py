"""Context stage callable for the Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookRequest, HookResult
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import (
    ContextFrame,
    ContextPort,
    ContextRequest,
    ContextStep,
    PromptStep,
    ThinkContractError,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)

PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")


MethodT = TypeVar("MethodT")


def _require_port_method(method: MethodT, name: str, /) -> MethodT:
    if not callable(method):
        raise ThinkContractError(f"ContextPort.{name} must be callable")
    return method


@dataclass(frozen=True, slots=True)
class ContextNode(
    Generic[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
):
    """Load one context snapshot after the Prompt Hook activation."""

    context_port: (
        ContextPort[
            ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
            ContextFrame[ContextSnapshotT],
        ]
        | None
    )

    def __post_init__(self) -> None:
        if self.context_port is None:
            raise ThinkContractError("context requires a ContextPort")
        try:
            method = self.context_port.load_context
        except AttributeError as error:
            raise ThinkContractError("context requires a ContextPort") from error
        _require_port_method(method, "load_context")

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Values[HookGraphValue]:
        request_value = values["request"]
        if type(request_value) is not ThinkRequest:
            raise ThinkContractError("context input must be a ThinkRequest")
        request = cast(ThinkRequest[PayloadT, HookStateT], request_value)

        hook_value = values["hook_result"]
        if type(hook_value) is not HookResult:
            raise ThinkContractError("context input must be a HookResult")
        hook_result = cast(HookResult[ThinkFrame[ThinkStep, HookStateT], HookGraphValue], hook_value)
        frame_value = hook_result.value
        if type(frame_value) is not ThinkFrame:
            raise ThinkContractError("context HookResult must contain a ThinkFrame")
        frame = frame_value
        if frame.hook_state != request.hook_state:
            raise ThinkContractError("context HookResult state does not match the ThinkRequest state")
        raw_step = frame.step
        if type(raw_step) is not PromptStep:
            raise ThinkContractError("context requires a PromptStep")
        # Standalone stage tests and generic callers may omit the optional
        # provenance field on HookResult.  Family graph execution supplies it
        # and is checked whenever present; omitting it must not weaken the
        # structural frame/step/state checks above.
        if hook_result.node_id is not None and hook_result.node_id != GraphNodeId("prompt"):
            raise ThinkContractError("context requires a HookResult produced by prompt")
        step = cast(PromptStep[SystemPromptT, PlaceholderT, UserPromptT], raw_step)
        prompt = step.prompt
        context_request = ContextRequest(request, prompt)

        port = cast(
            ContextPort[
                ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
                ContextFrame[ContextSnapshotT],
            ],
            self.context_port,
        )
        context_value = await port.load_context(context_request)
        if type(context_value) is not ContextFrame:
            raise ThinkContractError("ContextPort.load_context must return a ContextFrame")
        context = context_value
        next_frame = ThinkFrame(ContextStep(prompt, context), frame.hook_state)
        return Graph.values(hook_request=HookRequest(next_frame, frame.hook_state, GraphNodeId("context")))


__all__ = ["ContextNode"]

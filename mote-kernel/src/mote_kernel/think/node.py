"""Assembly owner for the public Think graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import HookGraphValue, HookRequest, HookResult
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.state.graph_state import GraphDefinitionId, GraphNodeId
from mote_kernel.think.command import CommandNode
from mote_kernel.think.compact import CompactNode
from mote_kernel.think.context import ContextNode
from mote_kernel.think.contract import (
    CommandPort,
    CompactedContext,
    CompactPort,
    CompactRequest,
    CompactStep,
    ContextFrame,
    ContextPort,
    ContextRequest,
    ContextStep,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    ModelBinding,
    PromptPort,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
    CommandStep,
    PromptStep,
    ThinkRoute,
)
from mote_kernel.think.inference import InferenceNode
from mote_kernel.think.prompt import PromptNode

ConfigT = TypeVar("ConfigT")
PriorityConfigT = TypeVar("PriorityConfigT")
PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT")
HookCommandT = TypeVar("HookCommandT")
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")
CommandT = TypeVar("CommandT")


def _think_route_for_result(
    result: HookResult[ThinkFrame[ThinkStep, HookGraphValue], HookGraphValue],
    /,
) -> str:
    """Map one admitted Think Hook result to its fixed successor token."""

    if type(result) is not HookResult:
        raise ThinkContractError("route selector requires a HookResult")
    frame = result.value
    if type(frame) is not ThinkFrame:
        raise ThinkContractError("route selector requires a ThinkFrame")
    step_type = type(frame.step)
    if step_type is PromptStep:
        return ThinkRoute.CONTEXT.value
    if step_type is ContextStep:
        return ThinkRoute.COMPACT.value
    if step_type is CompactStep:
        return ThinkRoute.INFERENCE.value
    if step_type is InferenceStep:
        return ThinkRoute.COMMAND.value
    if step_type is CommandStep:
        return ThinkRoute.FINISH.value
    raise ThinkContractError("route selector received an unknown ThinkStep")


@dataclass(frozen=True, slots=True)
class _RouteNode:
    """Private compatibility projector for a typed Think Hook result.

    ``ThinkNode`` no longer installs a second routing node: its containing
    graph owns the transition.  This callable remains useful to internal
    diagnostics and preserves the small, deterministic step-to-route proof
    without becoming another public graph API.
    """

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Outcome[HookGraphValue]:
        try:
            raw_result = values["result"]
        except (KeyError, TypeError) as error:
            raise ThinkContractError("route input is missing 'result'") from error
        if type(raw_result) is not HookResult:
            raise ThinkContractError("route input must be a HookResult")
        result = cast(HookResult[ThinkFrame[ThinkStep, HookGraphValue], HookGraphValue], raw_result)
        return Graph.success(Graph.values(hook_result=result), route=_think_route_for_result(result))


class ThinkNode(
    Graph[HookGraphValue],
    Generic[ConfigT, PriorityConfigT, HookStateT, HookCommandT],
):
    """The public five-stage Think graph with one shared Hook child.

    ``Graph`` remains the sole execution engine.  This class only performs
    assembly: the five stage callables and one real ``HookNode`` are declared
    as a single nested-graph boundary.  The Hook returns the identity of the
    business stage it processed; the static conditional edges below choose the
    next stage directly.
    """

    __slots__ = ("_hook",)

    def __init__(
        self,
        definition_id: str,
        *,
        version: int = 1,
        prompt_port: PromptPort[PayloadT, SystemPromptT, PlaceholderT, UserPromptT] | None,
        context_port: ContextPort[
            ContextRequest[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT],
            ContextFrame[ContextSnapshotT],
        ]
        | None,
        compact_port: CompactPort[
            CompactRequest[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
            CompactedContext[CompactedSnapshotT],
        ]
        | None,
        inference_port: InferencePort[
            InferenceRequest[SystemPromptT, PlaceholderT, UserPromptT, CompactedSnapshotT],
            InferenceResult[ModelOutputT],
        ]
        | None,
        command_port: CommandPort[InferenceResult[ModelOutputT], ThinkCoreResult[CommandT]] | None,
        model_binding: ModelBinding,
        hook: HookNode[
            ConfigT,
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ]
        | None,
    ) -> None:
        # The shared child is a real HookNode, not a Graph/callable with a
        # matching output shell.  Perform this check before touching the
        # parent builder so a failed assembly leaves no partial definition.
        if type(hook) is not HookNode:
            raise ThinkContractError("ThinkNode requires one shared HookNode")
        hook_slot = hook.slot
        if type(hook_slot) is not HookSlotId:
            raise ThinkContractError("ThinkNode shared hook must expose a HookSlotId")
        if (
            hook_slot.definition_id != GraphDefinitionId(definition_id)
            or int(hook_slot.definition_version) != version
            or hook_slot.node_id != GraphNodeId("hook")
            or hook_slot.stage is not HookStage.AFTER_NODE
        ):
            raise ThinkContractError("ThinkNode shared HookSlotId does not match its definition")

        # Constructing stage callables performs all capability/model checks;
        # no Graph.add_node call happens until every one has succeeded.
        prompt = PromptNode[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT](prompt_port)
        context = ContextNode[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT](
            context_port
        )
        compact = CompactNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
        ](compact_port)
        inference = InferenceNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            CompactedSnapshotT,
            ModelOutputT,
        ](inference_port, model_binding)
        command = CommandNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            CompactedSnapshotT,
            ModelOutputT,
            CommandT,
        ](command_port)
        super().__init__(definition_id, version=version)
        self._hook = hook

        request_type = cast(type[HookGraphValue], ThinkRequest)
        hook_request_type = cast(type[HookGraphValue], HookRequest)
        request_input = Graph.graph_input("request", request_type)

        self.add_node(
            "prompt",
            prompt,
            inputs={"request": request_input},
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "context",
            context,
            inputs={
                "request": request_input,
                "hook_result": Graph.node_output("result"),
            },
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "compact",
            compact,
            inputs={"hook_result": Graph.node_output("result")},
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "inference",
            inference,
            inputs={"hook_result": Graph.node_output("result")},
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "command",
            command,
            inputs={"hook_result": Graph.node_output("result")},
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "hook",
            hook,
            inputs={"request": Graph.node_output("hook_request")},
        )
        for business_node in ("prompt", "context", "compact", "inference", "command"):
            self.add_edge(business_node, "hook")
        self.add_edge("hook", ThinkRoute.CONTEXT.value, "context")
        self.add_edge("hook", ThinkRoute.COMPACT.value, "compact")
        self.add_edge("hook", ThinkRoute.INFERENCE.value, "inference")
        self.add_edge("hook", ThinkRoute.COMMAND.value, "command")
        self.add_edge("hook", ThinkRoute.FINISH.value, Graph.END)
        self.set_route_selector(
            "hook",
            Graph.result_selector(
                "result",
                cast(type[HookResult[ThinkFrame[ThinkStep, HookGraphValue], HookGraphValue]], HookResult),
                _think_route_for_result,
                routes=(
                    ThinkRoute.CONTEXT.value,
                    ThinkRoute.COMPACT.value,
                    ThinkRoute.INFERENCE.value,
                    ThinkRoute.COMMAND.value,
                    ThinkRoute.FINISH.value,
                ),
            ),
        )
        self.set_outputs({"result": Graph.node_output("hook", "result")})

    @property
    def hook(
        self,
    ) -> HookNode[
        ConfigT,
        PriorityConfigT,
        ThinkFrame[ThinkStep, HookStateT],
        HookStateT,
        HookCommandT,
    ]:
        """Return the one shared HookNode installed during assembly."""

        return self._hook


__all__ = ["ThinkNode"]

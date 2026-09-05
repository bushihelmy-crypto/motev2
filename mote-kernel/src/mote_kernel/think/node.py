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
    CommandStep,
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
    PromptStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkRoute,
    ThinkStep,
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


@dataclass(frozen=True, slots=True)
class _RouteNode:
    """Pass the Hook result through and select Think's next fixed edge."""

    async def __call__(
        self,
        values: Graph.Values[HookGraphValue],
        /,
    ) -> Graph.Outcome[HookGraphValue]:
        raw_result = values["result"]
        if type(raw_result) is not HookResult:
            raise ThinkContractError("route input must be a HookResult")
        hook_result = cast(HookResult[ThinkFrame[ThinkStep, HookGraphValue], HookGraphValue], raw_result)
        raw_frame = hook_result.value
        if type(raw_frame) is not ThinkFrame:
            raise ThinkContractError("route HookResult must contain a ThinkFrame")
        frame = raw_frame
        step_type = type(frame.step)
        if step_type is PromptStep:
            route = ThinkRoute.CONTEXT
        elif step_type is ContextStep:
            route = ThinkRoute.COMPACT
        elif step_type is CompactStep:
            route = ThinkRoute.INFERENCE
        elif step_type is InferenceStep:
            route = ThinkRoute.COMMAND
        elif step_type is CommandStep:
            route = ThinkRoute.FINISH
        else:
            raise ThinkContractError("route received an unknown ThinkStep")
        return Graph.success(Graph.values(hook_result=hook_result), route=route.value)


class ThinkNode(
    Graph[HookGraphValue],
    Generic[ConfigT, PriorityConfigT, HookStateT, HookCommandT],
):
    """The public five-stage Think graph with one shared Hook child.

    ``Graph`` remains the sole execution engine.  This class only performs
    assembly: the five stage callables, one real ``HookNode`` and one route
    callable are declared as a single nested-graph boundary.
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
        route = _RouteNode()

        super().__init__(definition_id, version=version)
        self._hook = hook

        request_type = cast(type[HookGraphValue], ThinkRequest)
        hook_request_type = cast(type[HookGraphValue], HookRequest)
        hook_result_type = cast(type[HookGraphValue], HookResult)
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
                "hook_result": Graph.node_output("hook_result"),
            },
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "compact",
            compact,
            inputs={"hook_result": Graph.node_output("hook_result")},
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "inference",
            inference,
            inputs={"hook_result": Graph.node_output("hook_result")},
            outputs={"hook_request": hook_request_type},
        )
        self.add_node(
            "command",
            command,
            inputs={"hook_result": Graph.node_output("hook_result")},
            outputs={"hook_request": hook_request_type},
        )
        self.add_node("hook", hook, inputs={"request": Graph.node_output("hook_request")})
        self.add_node(
            "route",
            route,
            inputs={"result": Graph.node_output("hook", "result")},
            outputs={"hook_result": hook_result_type},
        )

        for business_node in ("prompt", "context", "compact", "inference", "command"):
            self.add_edge(business_node, "hook")
        self.add_edge("hook", "route")
        self.add_conditional_edge("route", ThinkRoute.CONTEXT.value, "context")
        self.add_conditional_edge("route", ThinkRoute.COMPACT.value, "compact")
        self.add_conditional_edge("route", ThinkRoute.INFERENCE.value, "inference")
        self.add_conditional_edge("route", ThinkRoute.COMMAND.value, "command")
        self.add_conditional_edge("route", ThinkRoute.FINISH.value, Graph.END)
        self.set_outputs({"result": Graph.node_output("route", "hook_result")})

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

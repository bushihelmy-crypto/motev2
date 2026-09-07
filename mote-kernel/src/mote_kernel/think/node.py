"""Assembly owner for the public Think graph."""

from __future__ import annotations

from typing import Generic, TypeVar, cast

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    TypedInputBinding,
)
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import HookGraphValue, HookRequest, HookResult
from mote_kernel.hooks.identity import HookStage
from mote_kernel.state.graph_state import GraphDefinitionId, GraphNodeId
from mote_kernel.think.command import CommandNode
from mote_kernel.think.compact import CompactNode
from mote_kernel.think.context import ContextNode
from mote_kernel.think.contract import (
    CommandNodeInput,
    CommandPort,
    CompactedContext,
    CompactNodeInput,
    CompactPort,
    CompactRequest,
    CompactStep,
    ContextFrame,
    ContextNodeInput,
    ContextPort,
    ContextRequest,
    ContextStep,
    InferenceNodeInput,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    PromptPort,
    PromptStep,
    RouterNodeInput,
    RouterPort,
    RouterRequest,
    RouterStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)
from mote_kernel.think.inference import InferenceNode
from mote_kernel.think.prompt import PromptNode
from mote_kernel.think.router import RouterNode

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


class ThinkNode(
    Graph[HookGraphValue],
    Generic[ConfigT, PriorityConfigT, HookStateT, HookCommandT],
):
    """The public six-stage Think graph with one shared Hook child.

    ``Graph`` remains the sole execution engine.  This class only performs
    assembly: the six stage callables and one real ``HookNode`` are declared
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
        hook_state_type: type[HookStateT],
        hook: HookNode[
            ConfigT,
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ],
    ) -> None:
        # The shared child is a real HookNode, not a Graph/callable with a
        # matching output shell.  Perform this check before touching the
        # parent builder so a failed assembly leaves no partial definition.
        if type(hook) is not HookNode:
            raise ThinkContractError("ThinkNode requires one shared HookNode")
        if not issubclass(hook_state_type, HookGraphValue):
            raise ThinkContractError("ThinkNode hook_state_type must be a concrete HookGraphValue class")
        if hook_state_type is HookGraphValue:
            raise ThinkContractError("ThinkNode hook_state_type must be a concrete HookGraphValue class")
        hook_slot = hook.slot
        if (
            hook_slot.definition_id != GraphDefinitionId(definition_id)
            or int(hook_slot.definition_version) != version
            or hook_slot.node_id != GraphNodeId("hook")
            or hook_slot.stage is not HookStage.AFTER_NODE
        ):
            raise ThinkContractError("ThinkNode shared HookSlotId does not match its definition")
        hook_admission = hook.payload_admission
        if hook_admission.state_type is not hook_state_type:
            raise ThinkContractError("ThinkNode shared Hook state type does not match Think admission")

        # Constructing stage callables performs all capability checks;
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
        router = RouterNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
        ](router_port)
        inference = InferenceNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            CompactedSnapshotT,
            ModelOutputT,
        ](inference_port)
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

        request_input = cast(
            GraphInputRef[ThinkRequest[PayloadT, HookStateT]],
            Graph.graph_input("request", ThinkRequest),
        )
        request_binding = Graph.bind("request", request_input)
        prompt_output = self.add_node(
            "prompt",
            prompt,
            inputs=(request_binding,),
            input_type=ThinkRequest,
            materialize=lambda values: values.get(request_binding),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "hook",
            hook,
            inputs={"request": Graph.node_output(prompt_output)},
        )
        # Resolve the shared nested Hook boundary through Graph's generic
        # output API.  The returned descriptor is the child declaration's
        # identity, so every typed stage binding remains compiler-checked.
        hook_result_ref = self.output_ref("hook", "result")
        hook_result_source = Graph.node_output(hook_result_ref)
        context_hook_binding = cast(
            TypedInputBinding[
                HookResult[
                    ThinkFrame[PromptStep[SystemPromptT, PlaceholderT, UserPromptT], HookStateT],
                    HookGraphValue,
                ]
            ],
            Graph.bind("hook_result", hook_result_source),
        )
        compact_hook_binding = cast(
            TypedInputBinding[
                HookResult[
                    ThinkFrame[
                        ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
                        HookStateT,
                    ],
                    HookGraphValue,
                ]
            ],
            Graph.bind("hook_result", hook_result_source),
        )
        router_hook_binding = cast(
            TypedInputBinding[
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
                    HookGraphValue,
                ]
            ],
            Graph.bind("hook_result", hook_result_source),
        )
        inference_hook_binding = cast(
            TypedInputBinding[
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
                    HookGraphValue,
                ]
            ],
            Graph.bind("hook_result", hook_result_source),
        )
        command_hook_binding = cast(
            TypedInputBinding[
                HookResult[
                    ThinkFrame[
                        InferenceStep[
                            SystemPromptT,
                            PlaceholderT,
                            UserPromptT,
                            CompactedSnapshotT,
                            ModelOutputT,
                        ],
                        HookStateT,
                    ],
                    HookGraphValue,
                ]
            ],
            Graph.bind("hook_result", hook_result_source),
        )
        self.add_node(
            "context",
            context,
            inputs=(request_binding, context_hook_binding),
            input_type=ContextNodeInput,
            materialize=lambda values: ContextNodeInput(
                values.get(request_binding),
                values.get(context_hook_binding),
            ),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "compact",
            compact,
            inputs=(compact_hook_binding,),
            input_type=CompactNodeInput,
            materialize=lambda values: CompactNodeInput(values.get(compact_hook_binding)),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "router",
            router,
            inputs=(router_hook_binding,),
            input_type=RouterNodeInput,
            materialize=lambda values: RouterNodeInput(values.get(router_hook_binding)),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "inference",
            inference,
            inputs=(inference_hook_binding,),
            input_type=InferenceNodeInput,
            materialize=lambda values: InferenceNodeInput(values.get(inference_hook_binding)),
            output_name="hook_request",
            output_type=HookRequest,
        )
        self.add_node(
            "command",
            command,
            inputs=(command_hook_binding,),
            input_type=CommandNodeInput,
            materialize=lambda values: CommandNodeInput(values.get(command_hook_binding)),
            output_name="hook_request",
            output_type=HookRequest,
        )
        for business_node in ("prompt", "context", "compact", "router", "inference", "command"):
            self.add_edge(business_node, "hook")
        # The shared Hook returns the current business node identity as its
        # terminal route.  This graph decides what each identity means.
        self.add_edge("hook", "prompt", "context")
        self.add_edge("hook", "context", "compact")
        self.add_edge("hook", "compact", "router")
        self.add_edge("hook", "router", "inference")
        self.add_edge("hook", "inference", "command")
        self.add_edge("hook", "command", Graph.END)
        self.set_outputs({"result": hook_result_ref})

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

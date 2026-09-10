"""Assembly owner for the public Think graph."""

from __future__ import annotations

from typing import Generic, TypeVar, cast, overload

from mote_kernel.config import Config, ConfigActivation, ConfigSnapshotKey, require_config
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    TypedInputBinding,
)
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import HookActivationRequest, HookGraphValue, HookResult
from mote_kernel.hooks.failover import HookFailoverDecorator, HookFailoverDecorators
from mote_kernel.hooks.identity import HookStage
from mote_kernel.state.graph_state import GraphDefinitionId, GraphNodeId
from mote_kernel.think.command import CommandNode
from mote_kernel.think.compact import CompactNode
from mote_kernel.think.config import ThinkBinding
from mote_kernel.think.context import ContextNode
from mote_kernel.think.contract import (
    CommandPort,
    CompactedContext,
    CompactPort,
    CompactRequest,
    CompactStep,
    ContextFrame,
    ContextNodeInput,
    ContextPort,
    ContextRequest,
    ContextStep,
    InferencePort,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    PromptPort,
    PromptStep,
    RouterPort,
    RouterRequest,
    RouterStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)
from mote_kernel.think.failover import (
    FailoverPortDecorator,
    ThinkFailoverDecorators,
    normalize_think_failover_decorators,
)
from mote_kernel.think.identity import ThinkNodeId, ThinkValueName
from mote_kernel.think.inference import InferenceNode
from mote_kernel.think.prompt import PromptNode
from mote_kernel.think.router import RouterNode

PriorityConfigT = TypeVar("PriorityConfigT")
PayloadT = TypeVar("PayloadT")
HookStateT = TypeVar("HookStateT", bound=HookGraphValue)
HookCommandT = TypeVar("HookCommandT", bound=HookGraphValue)
SystemPromptT = TypeVar("SystemPromptT")
PlaceholderT = TypeVar("PlaceholderT")
UserPromptT = TypeVar("UserPromptT")
ContextSnapshotT = TypeVar("ContextSnapshotT")
CompactedSnapshotT = TypeVar("CompactedSnapshotT")
ModelOutputT = TypeVar("ModelOutputT")
CommandT = TypeVar("CommandT")


class ThinkNode(
    Graph[HookGraphValue],
    Generic[PriorityConfigT, HookStateT, HookCommandT],
):
    """The public six-stage Think graph with one shared Hook child.

    ``Graph`` remains the sole execution engine.  This class only performs
    assembly: the six stage callables and one real ``HookNode`` are declared
    as a single nested-graph boundary.  The Hook returns the identity of the
    business stage it processed; the static conditional edges below choose the
    next stage directly.
    """

    __slots__ = ("_failover", "_hook")

    @overload
    @classmethod
    def from_config(
        cls,
        config: Config,
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
        ],
        hook_failover: HookFailoverDecorators[
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ]
        | HookFailoverDecorator
        | None = None,
    ) -> ThinkNode[PriorityConfigT, HookStateT, HookCommandT]: ...

    @overload
    @classmethod
    def from_config(
        cls,
        config: Config,
        /,
        *,
        failover: FailoverPortDecorator | None = None,
        hook_failover: HookFailoverDecorators[
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ]
        | HookFailoverDecorator
        | None = None,
    ) -> ThinkNode[PriorityConfigT, HookStateT, HookCommandT]: ...

    @classmethod
    def from_config(
        cls,
        config: Config,
        /,
        *,
        failover: (
            ThinkFailoverDecorators[
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
            | None
        ) = None,
        hook_failover: HookFailoverDecorators[
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ]
        | HookFailoverDecorator
        | None = None,
    ) -> ThinkNode[PriorityConfigT, HookStateT, HookCommandT]:
        """Assemble Think from the complete config through its own projection."""

        config = require_config(config)
        selected = config.bind(
            ThinkBinding[
                PriorityConfigT,
                object,
                HookStateT,
                HookCommandT,
                object,
                object,
                object,
                object,
                object,
                object,
                object,
            ]()
        )
        hook: HookNode[
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ] = HookNode[
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ].from_config(config, selected.hook_slot, failover=hook_failover)
        return cls(
            str(selected.definition_id),
            version=int(selected.definition_version),
            prompt_port=selected.prompt_port,
            context_port=selected.context_port,
            compact_port=selected.compact_port,
            router_port=selected.router_port,
            inference_port=selected.inference_port,
            command_port=selected.command_port,
            hook_state_type=selected.hook_state_type,
            hook=hook,
            # The complete Config projection intentionally erases the
            # concrete Think payload classes at this root boundary.  The
            # constructor's typed Port arguments re-establish them below;
            # retain the caller's bundle after that same admission boundary.
            failover=cast(
                ThinkFailoverDecorators[
                    object,
                    HookStateT,
                    object,
                    object,
                    object,
                    object,
                    object,
                    object,
                    object,
                ]
                | FailoverPortDecorator
                | None,
                failover,
            ),
            assembly_snapshot_key=config.snapshot.key,
        )

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
            PriorityConfigT,
            ThinkFrame[ThinkStep, HookStateT],
            HookStateT,
            HookCommandT,
        ],
        failover: (
            ThinkFailoverDecorators[
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
            | None
        ) = None,
        assembly_snapshot_key: ConfigSnapshotKey | None = None,
    ) -> None:
        # The shared child is a real HookNode, not a Graph/callable with a
        # matching output shell.  Perform this check before touching the
        # parent builder so a failed assembly leaves no partial definition.
        if type(hook) is not HookNode:
            raise ThinkContractError("ThinkNode requires one shared HookNode")
        if assembly_snapshot_key is not None and type(assembly_snapshot_key) is not ConfigSnapshotKey:
            raise ThinkContractError("ThinkNode assembly snapshot key is malformed")
        if not issubclass(cast(type[object], hook_state_type), HookGraphValue):
            raise ThinkContractError("ThinkNode hook_state_type must be a concrete HookGraphValue class")
        if hook_state_type is HookGraphValue:
            raise ThinkContractError("ThinkNode hook_state_type must be a concrete HookGraphValue class")
        hook_slot = hook.slot
        if (
            hook_slot.definition_id != GraphDefinitionId(definition_id)
            or int(hook_slot.definition_version) != version
            or hook_slot.node_id != GraphNodeId(str(ThinkNodeId.HOOK))
            or hook_slot.stage is not HookStage.AFTER_NODE
        ):
            raise ThinkContractError("ThinkNode shared HookSlotId does not match its definition")
        hook_admission = hook.payload_admission
        if hook_admission.state_type is not hook_state_type:
            raise ThinkContractError("ThinkNode shared Hook state type does not match Think admission")

        decorators = normalize_think_failover_decorators(failover)
        # Each stage owns its one assembly-time Port decoration, so direct
        # stage construction has the same semantics as parent-graph assembly.

        # Constructing stage callables performs all capability checks;
        # no Graph.add_node call happens until every one has succeeded.
        prompt = PromptNode[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT](
            prompt_port,
            decorators.prompt,
            assembly_snapshot_key=assembly_snapshot_key,
        )
        context = ContextNode[PayloadT, HookStateT, SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT](
            context_port,
            decorators.context,
            assembly_snapshot_key=assembly_snapshot_key,
        )
        compact = CompactNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
        ](compact_port, decorators.compact, assembly_snapshot_key=assembly_snapshot_key)
        router = RouterNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            ContextSnapshotT,
            CompactedSnapshotT,
        ](router_port, decorators.router, assembly_snapshot_key=assembly_snapshot_key)
        inference = InferenceNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            CompactedSnapshotT,
            ModelOutputT,
        ](inference_port, decorators.inference, assembly_snapshot_key=assembly_snapshot_key)
        command = CommandNode[
            HookStateT,
            SystemPromptT,
            PlaceholderT,
            UserPromptT,
            CompactedSnapshotT,
            ModelOutputT,
            CommandT,
        ](command_port, decorators.command, assembly_snapshot_key=assembly_snapshot_key)
        super().__init__(definition_id, version=version)
        self._failover = decorators
        self._hook = hook

        request_input = cast(
            GraphInputRef[ThinkRequest[PayloadT, HookStateT]],
            Graph.graph_input(ThinkValueName.REQUEST, ThinkRequest),
        )
        request_binding = Graph.bind(ThinkValueName.REQUEST, request_input)
        prompt_output = self.add_node(
            ThinkNodeId.PROMPT,
            prompt,
            inputs=(request_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(request_binding),
                values.activation_config,
            ),
            output_name=ThinkValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ThinkNodeId.HOOK,
            hook,
            inputs={ThinkValueName.REQUEST: Graph.node_output(prompt_output)},
        )
        # Resolve the shared nested Hook boundary through Graph's generic
        # output API.  The returned descriptor is the child declaration's
        # identity, so every typed stage binding remains compiler-checked.
        hook_result_ref = self.output_ref(ThinkNodeId.HOOK, ThinkValueName.RESULT)
        hook_result_source = Graph.node_output(hook_result_ref)
        context_hook_binding = cast(
            TypedInputBinding[
                HookResult[
                    ThinkFrame[PromptStep[SystemPromptT, PlaceholderT, UserPromptT], HookStateT],
                    HookCommandT,
                ]
            ],
            Graph.bind(ThinkValueName.HOOK_RESULT, hook_result_source),
        )
        compact_hook_binding = cast(
            TypedInputBinding[
                HookResult[
                    ThinkFrame[
                        ContextStep[SystemPromptT, PlaceholderT, UserPromptT, ContextSnapshotT],
                        HookStateT,
                    ],
                    HookCommandT,
                ]
            ],
            Graph.bind(ThinkValueName.HOOK_RESULT, hook_result_source),
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
                    HookCommandT,
                ]
            ],
            Graph.bind(ThinkValueName.HOOK_RESULT, hook_result_source),
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
                    HookCommandT,
                ]
            ],
            Graph.bind(ThinkValueName.HOOK_RESULT, hook_result_source),
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
                    HookCommandT,
                ]
            ],
            Graph.bind(ThinkValueName.HOOK_RESULT, hook_result_source),
        )
        self.add_node(
            ThinkNodeId.CONTEXT,
            context,
            inputs=(request_binding, context_hook_binding),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                ContextNodeInput(
                    values.get(request_binding),
                    values.get(context_hook_binding),
                ),
                values.activation_config,
            ),
            output_name=ThinkValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ThinkNodeId.COMPACT,
            compact,
            inputs=(compact_hook_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(compact_hook_binding),
                values.activation_config,
            ),
            output_name=ThinkValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ThinkNodeId.ROUTER,
            router,
            inputs=(router_hook_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(router_hook_binding),
                values.activation_config,
            ),
            output_name=ThinkValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ThinkNodeId.INFERENCE,
            inference,
            inputs=(inference_hook_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(inference_hook_binding),
                values.activation_config,
            ),
            output_name=ThinkValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_node(
            ThinkNodeId.COMMAND,
            command,
            inputs=(command_hook_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(command_hook_binding),
                values.activation_config,
            ),
            output_name=ThinkValueName.HOOK_REQUEST,
            output_type=HookActivationRequest,
        )
        self.add_edge(Graph.START, ThinkNodeId.PROMPT)
        for business_node in (
            ThinkNodeId.PROMPT,
            ThinkNodeId.CONTEXT,
            ThinkNodeId.COMPACT,
            ThinkNodeId.ROUTER,
            ThinkNodeId.INFERENCE,
            ThinkNodeId.COMMAND,
        ):
            self.add_edge(business_node, ThinkNodeId.HOOK)
        # The shared Hook returns the current business node identity as its
        # terminal route.  This graph decides what each identity means.
        self.add_edge(ThinkNodeId.HOOK, ThinkNodeId.PROMPT, ThinkNodeId.CONTEXT)
        self.add_edge(ThinkNodeId.HOOK, ThinkNodeId.CONTEXT, ThinkNodeId.COMPACT)
        self.add_edge(ThinkNodeId.HOOK, ThinkNodeId.COMPACT, ThinkNodeId.ROUTER)
        self.add_edge(ThinkNodeId.HOOK, ThinkNodeId.ROUTER, ThinkNodeId.INFERENCE)
        self.add_edge(ThinkNodeId.HOOK, ThinkNodeId.INFERENCE, ThinkNodeId.COMMAND)
        self.add_edge(ThinkNodeId.HOOK, ThinkNodeId.COMMAND, Graph.END)
        self.set_outputs({ThinkValueName.RESULT: hook_result_ref})

    @property
    def hook(
        self,
    ) -> HookNode[
        PriorityConfigT,
        ThinkFrame[ThinkStep, HookStateT],
        HookStateT,
        HookCommandT,
    ]:
        """Return the one shared HookNode installed during assembly."""

        return self._hook


__all__ = ["ThinkNode"]

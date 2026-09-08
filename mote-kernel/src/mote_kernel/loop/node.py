"""Three-node ReAct loop assembled from canonical nested Graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import (
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
)
from mote_kernel.act.contract import (
    HookStateProjection as ActHookStateProjection,
)
from mote_kernel.act.node import ActNode
from mote_kernel.config import (
    Config,
    ConfigActivation,
    ConfigContractError,
    ConfigSelector,
    require_config,
)
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import GraphInputRef, NodeOutputRef
from mote_kernel.hooks.contract import HookGraphValue, HookPayloadAdmission, HookResult
from mote_kernel.loop.admission import ReActPayloadAdmission
from mote_kernel.loop.config import (
    ActToObserveBinding,
    ObserveToActBinding,
    ObserveToThinkBinding,
    ReActBinding,
    ReActNodeConfig,
    ReActPrepareBinding,
    ReActRouteBinding,
    ReActRuntimeConfig,
    ThinkToObserveBinding,
)
from mote_kernel.loop.contract import (
    ActToObserveProjector,
    ObserveRoutePolicy,
    ObserveToActProjector,
    ObserveToThinkProjector,
    ReActContractError,
    ReActRoute,
    ThinkToObserveProjector,
)
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.contract import (
    HookStateProjection as ObserveHookStateProjection,
)
from mote_kernel.observe.contract import (
    ObserveHookCommand,
    ObserveHookEnvelope,
    ObserveRequest,
)
from mote_kernel.observe.node import ObserveNode
from mote_kernel.think.contract import ThinkFrame, ThinkRequest, ThinkStep
from mote_kernel.think.node import ThinkNode

ObservePriorityConfigT = TypeVar("ObservePriorityConfigT")
ObserveStateT = TypeVar("ObserveStateT", bound=ObserveHookStateProjection)
ObserveHookCommandT = TypeVar("ObserveHookCommandT", bound=ObserveHookCommand)
ThinkPriorityConfigT = TypeVar("ThinkPriorityConfigT")
ThinkPayloadT = TypeVar("ThinkPayloadT")
ThinkStateT = TypeVar("ThinkStateT", bound=HookGraphValue)
ThinkHookCommandT = TypeVar("ThinkHookCommandT", bound=HookGraphValue)
ActPriorityConfigT = TypeVar("ActPriorityConfigT")
ActStateT = TypeVar("ActStateT", bound=ActHookStateProjection)
ActHookCommandT = TypeVar("ActHookCommandT", bound=ActHookCommand)
_ReActCapabilityT = TypeVar("_ReActCapabilityT")

_OBSERVE_COMPLETION_ROUTE = "write_observation"
_THINK_COMPLETION_ROUTE = "command"
_ACT_COMPLETION_ROUTE = "settle"


@dataclass(frozen=True, slots=True)
class _ReActOperations(
    Generic[
        ObserveStateT,
        ObserveHookCommandT,
        ThinkPayloadT,
        ThinkStateT,
        ThinkHookCommandT,
        ActStateT,
        ActHookCommandT,
    ]
):
    admission: ReActPayloadAdmission[
        ObserveStateT,
        ObserveHookCommandT,
        ThinkStateT,
        ThinkHookCommandT,
        ActStateT,
        ActHookCommandT,
    ]
    route_policy: ObserveRoutePolicy
    observe_to_act: ObserveToActProjector[ObserveHookCommandT]
    observe_to_think: ObserveToThinkProjector[ObserveHookCommandT, ThinkPayloadT, ThinkStateT]
    think_to_observe: ThinkToObserveProjector[ThinkStateT, ThinkHookCommandT, ObserveStateT]
    act_to_observe: ActToObserveProjector[ActHookCommandT, ObserveStateT]
    # The static values above are assembly fallbacks for graphs created
    # directly without a complete Config. Config-backed activations bind only
    # the one route/projector used by the current callable.
    definition_id: str = ""
    definition_version: int = 1

    def _admit_runtime_identity(
        self,
        selected: ReActRuntimeConfig,
        /,
    ) -> None:
        if type(selected) not in (ReActRuntimeConfig, ReActNodeConfig):
            raise ReActContractError("ReAct config binding returned an invalid projection")
        if self.definition_id and (
            str(selected.definition_id) != self.definition_id
            or int(selected.definition_version) != self.definition_version
        ):
            raise ReActContractError("ReAct config binding changed the compiled graph contract")

    def _bind_runtime_capability(
        self,
        config: Config | None,
        selector: ConfigSelector[ReActNodeConfig[_ReActCapabilityT]],
        fallback: _ReActCapabilityT,
        /,
    ) -> _ReActCapabilityT:
        if config is None:
            return fallback
        try:
            selected = config.bind(selector)
        except ConfigContractError as error:
            raise ReActContractError(str(error)) from error
        if type(selected) is not ReActNodeConfig:
            raise ReActContractError("ReAct config binding returned an invalid projection")
        self._admit_runtime_identity(selected)
        return selected.capability

    def _bind_prepare_identity(self, config: Config | None, /) -> None:
        if config is None:
            return
        try:
            selected = config.bind(ReActPrepareBinding())
        except ConfigContractError as error:
            raise ReActContractError(str(error)) from error
        self._admit_runtime_identity(selected)

    async def route_observe(
        self,
        activation: ConfigActivation[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
        /,
    ) -> Graph.SuccessOutcome[HookResult[ObserveHookEnvelope, ObserveHookCommandT]]:
        """Route one admitted Observe completion.

        A Config-only observation keeps the existing ``CONFIG -> END``
        business route.  Its successor Config remains execution activation
        metadata; it never changes topology or triggers graph reassembly.
        Mixed Config/non-Config batches retain their normal business route.
        """

        result = activation.value
        admitted = self.admission.admit_observe_boundary(result)
        config = activation.activation_config
        route_policy = self._bind_runtime_capability(
            config,
            ReActRouteBinding(),
            self.route_policy,
        )
        route = self.admission.select_route(route_policy, admitted)
        return Graph.success(Graph.values(result=admitted), route=route.value)

    async def prepare_observe(
        self,
        activation: ConfigActivation[ObserveRequest[ObserveStateT]],
        /,
    ) -> ObserveRequest[ObserveStateT]:
        """Admit the phase input before starting the nested Observe graph.

        Keeping one callable boundary ahead of the nested child is important
        for recovery: a newly activated phase can be proven from its graph
        input before its nested Observe activation is materialized.
        """

        request = activation.value
        self._bind_prepare_identity(activation.activation_config)
        return self.admission.observe.admit_request(request)

    async def project_observe_to_act(
        self,
        activation: ConfigActivation[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
        /,
    ) -> ActRequest:
        result = activation.value
        admitted = self.admission.admit_observe_boundary(result)
        config = activation.activation_config
        projector = self._bind_runtime_capability(
            config,
            ObserveToActBinding[ObserveHookCommandT](),
            self.observe_to_act,
        )
        return self.admission.project_observe_to_act(projector, admitted)

    async def project_observe_to_think(
        self,
        activation: ConfigActivation[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
        /,
    ) -> ThinkRequest[ThinkPayloadT, ThinkStateT]:
        result = activation.value
        admitted = self.admission.admit_observe_boundary(result)
        config = activation.activation_config
        projector = self._bind_runtime_capability(
            config,
            ObserveToThinkBinding[ObserveHookCommandT, ThinkPayloadT, ThinkStateT](),
            self.observe_to_think,
        )
        return self.admission.project_observe_to_think(projector, admitted)

    async def project_think_to_observe(
        self,
        activation: ConfigActivation[HookResult[ThinkFrame[ThinkStep, ThinkStateT], ThinkHookCommandT]],
        /,
    ) -> ObserveRequest[ObserveStateT]:
        result = activation.value
        admitted = self.admission.admit_think_boundary(result)
        projector = self._bind_runtime_capability(
            activation.activation_config,
            ThinkToObserveBinding[ThinkStateT, ThinkHookCommandT, ObserveStateT](),
            self.think_to_observe,
        )
        return self.admission.project_think_to_observe(projector, admitted)

    async def project_act_to_observe(
        self,
        activation: ConfigActivation[HookResult[ActHookEnvelope, ActHookCommandT]],
        /,
    ) -> ObserveRequest[ObserveStateT]:
        result = activation.value
        admitted = self.admission.admit_act_boundary(result)
        projector = self._bind_runtime_capability(
            activation.activation_config,
            ActToObserveBinding[ActHookCommandT, ObserveStateT](),
            self.act_to_observe,
        )
        return self.admission.project_act_to_observe(projector, admitted)


def _observe_phase(
    definition_id: str,
    version: int,
    observe: ObserveNode[ObservePriorityConfigT, ObserveStateT, ObserveHookCommandT],
    operations: _ReActOperations[
        ObserveStateT,
        ObserveHookCommandT,
        ThinkPayloadT,
        ThinkStateT,
        ThinkHookCommandT,
        ActStateT,
        ActHookCommandT,
    ],
) -> tuple[
    Graph[HookGraphValue],
    NodeOutputRef[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
]:
    phase = Graph[HookGraphValue](f"{definition_id}.observe-phase", version=version)
    request_input = cast(
        GraphInputRef[ObserveRequest[ObserveStateT]],
        Graph.graph_input("request", ObserveRequest),
    )
    request_binding = Graph.bind("request", request_input)
    prepared_request = phase.add_node(
        "prepare",
        operations.prepare_observe,
        inputs=(request_binding,),
        input_type=ConfigActivation,
        materialize=lambda values: ConfigActivation(
            values.get(request_binding),
            values.activation_config,
        ),
        output_name="request",
        output_type=ObserveRequest,
    )
    phase.add_node("run", observe, inputs={"request": prepared_request})
    result_ref = cast(
        NodeOutputRef[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
        phase.output_ref("run", "result"),
    )
    route_binding = Graph.bind("result", result_ref)
    routed_result = phase.add_node(
        "route",
        operations.route_observe,
        inputs=(route_binding,),
        input_type=ConfigActivation,
        materialize=lambda values: ConfigActivation(
            values.get(route_binding),
            values.activation_config,
        ),
        output_name="result",
        output_type=HookResult,
    )
    phase.add_edge(Graph.START, "prepare")
    phase.add_edge("prepare", "run")
    phase.add_edge("run", _OBSERVE_COMPLETION_ROUTE, "route")
    for route in (ReActRoute.CONFIG, ReActRoute.ASSISTANT, ReActRoute.THINK, ReActRoute.ACT):
        phase.add_edge("route", route.value, Graph.END)
    phase.set_outputs({"result": routed_result})
    return phase, routed_result


def _think_phase(
    definition_id: str,
    version: int,
    think: ThinkNode[ThinkPriorityConfigT, ThinkStateT, ThinkHookCommandT],
    operations: _ReActOperations[
        ObserveStateT,
        ObserveHookCommandT,
        ThinkPayloadT,
        ThinkStateT,
        ThinkHookCommandT,
        ActStateT,
        ActHookCommandT,
    ],
) -> tuple[Graph[HookGraphValue], NodeOutputRef[ObserveRequest[ObserveStateT]]]:
    phase = Graph[HookGraphValue](f"{definition_id}.think-phase", version=version)
    result_input = cast(
        GraphInputRef[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
        Graph.graph_input("result", HookResult),
    )
    result_binding = Graph.bind("result", result_input)
    request = phase.add_node(
        "project",
        operations.project_observe_to_think,
        inputs=(result_binding,),
        input_type=ConfigActivation,
        materialize=lambda values: ConfigActivation(
            values.get(result_binding),
            values.activation_config,
        ),
        output_name="request",
        output_type=ThinkRequest,
    )
    phase.add_node("run", think, inputs={"request": request})
    think_result_ref = cast(
        NodeOutputRef[HookResult[ThinkFrame[ThinkStep, ThinkStateT], ThinkHookCommandT]],
        phase.output_ref("run", "result"),
    )
    think_result_binding = Graph.bind("result", Graph.node_output(think_result_ref))
    next_request = phase.add_node(
        "next_observe",
        operations.project_think_to_observe,
        inputs=(think_result_binding,),
        input_type=ConfigActivation,
        materialize=lambda values: ConfigActivation(
            values.get(think_result_binding),
            values.activation_config,
        ),
        output_name="request",
        output_type=ObserveRequest,
    )
    phase.add_edge("project", "run")
    phase.add_edge("run", _THINK_COMPLETION_ROUTE, "next_observe")
    phase.add_edge("next_observe", Graph.END)
    phase.set_outputs({"request": next_request})
    return phase, next_request


def _act_phase(
    definition_id: str,
    version: int,
    act: ActNode[ActPriorityConfigT, ActStateT, ActHookCommandT],
    operations: _ReActOperations[
        ObserveStateT,
        ObserveHookCommandT,
        ThinkPayloadT,
        ThinkStateT,
        ThinkHookCommandT,
        ActStateT,
        ActHookCommandT,
    ],
) -> tuple[Graph[HookGraphValue], NodeOutputRef[ObserveRequest[ObserveStateT]]]:
    phase = Graph[HookGraphValue](f"{definition_id}.act-phase", version=version)
    result_input = cast(
        GraphInputRef[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
        Graph.graph_input("result", HookResult),
    )
    result_binding = Graph.bind("result", result_input)
    request = phase.add_node(
        "project",
        operations.project_observe_to_act,
        inputs=(result_binding,),
        input_type=ConfigActivation,
        materialize=lambda values: ConfigActivation(
            values.get(result_binding),
            values.activation_config,
        ),
        output_name="request",
        output_type=ActRequest,
    )
    phase.add_node("run", act, inputs={"request": request})
    act_result_ref = cast(
        NodeOutputRef[HookResult[ActHookEnvelope, ActHookCommandT]],
        phase.output_ref("run", "result"),
    )
    act_result_binding = Graph.bind("result", Graph.node_output(act_result_ref))
    next_request = phase.add_node(
        "next_observe",
        operations.project_act_to_observe,
        inputs=(act_result_binding,),
        input_type=ConfigActivation,
        materialize=lambda values: ConfigActivation(
            values.get(act_result_binding),
            values.activation_config,
        ),
        output_name="request",
        output_type=ObserveRequest,
    )
    phase.add_edge("project", "run")
    phase.add_edge("run", _ACT_COMPLETION_ROUTE, "next_observe")
    phase.add_edge("next_observe", Graph.END)
    phase.set_outputs({"request": next_request})
    return phase, next_request


class ReActNode(
    Graph[HookGraphValue],
    Generic[ObserveStateT, ThinkPayloadT, ThinkStateT, ActStateT],
):
    """Compose one durable Observe/Think/Act loop with three direct nodes."""

    __slots__ = ()

    @classmethod
    def from_config(
        cls,
        config: Config,
        /,
    ) -> ReActNode[ObserveStateT, ThinkPayloadT, ThinkStateT, ActStateT]:
        """Assemble the loop from one complete config snapshot.

        The same complete object is deliberately forwarded to each child.  A
        child then performs its own domain-owned projection (and its Hook does
        the same for its slot); no parent extracts or re-wraps a child's
        capabilities.
        """

        config = require_config(config)
        selected = config.bind(
            ReActBinding[
                ObserveStateT,
                ObserveHookCommand,
                ThinkPayloadT,
                ThinkStateT,
                HookGraphValue,
                ActStateT,
                ActHookCommand,
            ]()
        )
        observe = ObserveNode[
            object,
            ObserveStateT,
            ObserveHookCommand,
        ].from_config(config)
        think = ThinkNode[
            object,
            ThinkStateT,
            HookGraphValue,
        ].from_config(config)
        act = ActNode[
            object,
            ActStateT,
            ActHookCommand,
        ].from_config(config)
        return cls(
            str(selected.definition_id),
            version=int(selected.definition_version),
            observe=observe,
            think=think,
            act=act,
            route_policy=selected.route_policy,
            observe_to_act=selected.observe_to_act,
            observe_to_think=selected.observe_to_think,
            think_to_observe=selected.think_to_observe,
            act_to_observe=selected.act_to_observe,
        )

    def __init__(
        self,
        definition_id: str,
        *,
        version: int = 1,
        observe: ObserveNode[ObservePriorityConfigT, ObserveStateT, ObserveHookCommandT],
        think: ThinkNode[ThinkPriorityConfigT, ThinkStateT, ThinkHookCommandT],
        act: ActNode[ActPriorityConfigT, ActStateT, ActHookCommandT],
        route_policy: ObserveRoutePolicy,
        observe_to_act: ObserveToActProjector[ObserveHookCommandT],
        observe_to_think: ObserveToThinkProjector[ObserveHookCommandT, ThinkPayloadT, ThinkStateT],
        think_to_observe: ThinkToObserveProjector[ThinkStateT, ThinkHookCommandT, ObserveStateT],
        act_to_observe: ActToObserveProjector[ActHookCommandT, ObserveStateT],
    ) -> None:
        if type(observe) is not ObserveNode:
            raise ReActContractError("ReActNode requires an ObserveNode")
        if type(think) is not ThinkNode:
            raise ReActContractError("ReActNode requires a ThinkNode")
        if type(act) is not ActNode:
            raise ReActContractError("ReActNode requires an ActNode")
        if not callable(route_policy):
            raise ReActContractError("ReActNode requires a synchronous route policy")
        if not callable(observe_to_act):
            raise ReActContractError("ReActNode requires a synchronous Observe-to-Act projector")
        if not callable(observe_to_think):
            raise ReActContractError("ReActNode requires a synchronous Observe-to-Think projector")
        if not callable(think_to_observe):
            raise ReActContractError("ReActNode requires a synchronous Think-to-Observe projector")
        if not callable(act_to_observe):
            raise ReActContractError("ReActNode requires a synchronous Act-to-Observe projector")

        observe_hook_admission = observe.hook.payload_admission
        if type(observe_hook_admission) is not HookPayloadAdmission:
            raise ReActContractError("ObserveNode must expose a HookPayloadAdmission")
        if observe_hook_admission.value_type is not ObserveHookEnvelope:
            raise ReActContractError("ObserveNode Hook must carry ObserveHookEnvelope values")
        observe_admission = observe_hook_admission.transition_admission
        if type(observe_admission) is not ObservePayloadAdmission:
            raise ReActContractError("ObserveNode must expose its Observe payload admission")
        if observe_hook_admission.state_type is not observe_admission.hook_state_type:
            raise ReActContractError("ObserveNode Hook state type does not match Observe admission")
        if observe_hook_admission.command_type is not observe_admission.hook_command_type:
            raise ReActContractError("ObserveNode Hook command type does not match Observe admission")
        think_hook_admission = think.hook.payload_admission
        if type(think_hook_admission) is not HookPayloadAdmission:
            raise ReActContractError("ThinkNode must expose a HookPayloadAdmission")
        if think_hook_admission.value_type is not ThinkFrame:
            raise ReActContractError("ThinkNode must expose a ThinkFrame Hook admission")
        act_hook_admission = act.hook.payload_admission
        if type(act_hook_admission) is not HookPayloadAdmission:
            raise ReActContractError("ActNode must expose a HookPayloadAdmission")
        if act_hook_admission.value_type is not ActHookEnvelope:
            raise ReActContractError("ActNode Hook must carry ActHookEnvelope values")
        act_admission = act_hook_admission.transition_admission
        if type(act_admission) is not ActPayloadAdmission:
            raise ReActContractError("ActNode must expose its Act payload admission")
        if act_hook_admission.state_type is not act_admission.hook_state_type:
            raise ReActContractError("ActNode Hook state type does not match Act admission")
        if act_hook_admission.command_type is not act_admission.hook_command_type:
            raise ReActContractError("ActNode Hook command type does not match Act admission")

        admission = ReActPayloadAdmission[
            ObserveStateT,
            ObserveHookCommandT,
            ThinkStateT,
            ThinkHookCommandT,
            ActStateT,
            ActHookCommandT,
        ](
            observe_admission,
            think_hook_admission.state_type,
            think_hook_admission.command_type,
            act_admission,
        )
        operations = _ReActOperations[
            ObserveStateT,
            ObserveHookCommandT,
            ThinkPayloadT,
            ThinkStateT,
            ThinkHookCommandT,
            ActStateT,
            ActHookCommandT,
        ](
            admission,
            route_policy,
            observe_to_act,
            observe_to_think,
            think_to_observe,
            act_to_observe,
            definition_id=definition_id,
            definition_version=version,
        )
        observe_phase, observe_result = _observe_phase(
            definition_id,
            version,
            observe,
            operations,
        )
        think_phase, _think_request = _think_phase(definition_id, version, think, operations)
        act_phase, _act_request = _act_phase(definition_id, version, act, operations)

        super().__init__(definition_id, version=version)
        self.add_node(
            "observe",
            observe_phase,
            inputs={"request": Graph.node_output("request")},
        )
        self.add_node(
            "think",
            think_phase,
            inputs={"result": Graph.node_output(observe_result)},
        )
        self.add_node(
            "act",
            act_phase,
            inputs={"result": Graph.node_output(observe_result)},
        )
        self.add_edge(Graph.START, "observe")
        self.add_edge("think", "observe")
        self.add_edge("act", "observe")
        self.add_edge("observe", ReActRoute.CONFIG.value, Graph.END)
        self.add_edge("observe", ReActRoute.ASSISTANT.value, Graph.END)
        self.add_edge("observe", ReActRoute.THINK.value, "act")
        self.add_edge("observe", ReActRoute.ACT.value, "think")
        self.set_outputs({"result": self.output_ref("observe", "result")})


__all__ = ["ReActNode"]

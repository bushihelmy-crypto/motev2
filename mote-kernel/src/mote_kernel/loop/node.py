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
from mote_kernel.act.identity import ActNodeId
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
    ReActNodeId,
    ReActPhaseNodeId,
    ReActRoute,
    ReActValueName,
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
from mote_kernel.observe.identity import ObserveNodeId
from mote_kernel.observe.node import ObserveNode
from mote_kernel.think.contract import ThinkFrame, ThinkRequest, ThinkStep
from mote_kernel.think.identity import ThinkNodeId
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
        return Graph.success(Graph.values(**{ReActValueName.RESULT: admitted}), route=route)

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
        observe_phase = Graph[HookGraphValue](f"{definition_id}.observe-phase", version=version)
        observe_request_input = cast(
            GraphInputRef[ObserveRequest[ObserveStateT]],
            Graph.graph_input(ReActValueName.REQUEST, ObserveRequest),
        )
        observe_request_binding = Graph.bind(ReActValueName.REQUEST, observe_request_input)
        prepared_request = observe_phase.add_node(
            ReActPhaseNodeId.PREPARE,
            operations.prepare_observe,
            inputs=(observe_request_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(observe_request_binding),
                values.activation_config,
            ),
            output_name=ReActValueName.REQUEST,
            output_type=ObserveRequest,
        )
        observe_phase.add_node(
            ReActPhaseNodeId.RUN,
            observe,
            inputs={ReActValueName.REQUEST: prepared_request},
        )
        observe_result = cast(
            NodeOutputRef[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
            observe_phase.output_ref(ReActPhaseNodeId.RUN, ReActValueName.RESULT),
        )
        route_binding = Graph.bind(ReActValueName.RESULT, observe_result)
        routed_result = observe_phase.add_node(
            ReActPhaseNodeId.ROUTE,
            operations.route_observe,
            inputs=(route_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(route_binding),
                values.activation_config,
            ),
            output_name=ReActValueName.RESULT,
            output_type=HookResult,
        )
        observe_phase.add_edge(Graph.START, ReActPhaseNodeId.PREPARE)
        observe_phase.add_edge(ReActPhaseNodeId.PREPARE, ReActPhaseNodeId.RUN)
        observe_phase.add_edge(
            ReActPhaseNodeId.RUN,
            ObserveNodeId.WRITE_OBSERVATION,
            ReActPhaseNodeId.ROUTE,
        )
        for route in (ReActRoute.CONFIG, ReActRoute.ASSISTANT, ReActRoute.THINK, ReActRoute.ACT):
            observe_phase.add_edge(ReActPhaseNodeId.ROUTE, route, Graph.END)
        observe_phase.set_outputs({ReActValueName.RESULT: routed_result})

        think_phase = Graph[HookGraphValue](f"{definition_id}.think-phase", version=version)
        think_result_input = cast(
            GraphInputRef[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
            Graph.graph_input(ReActValueName.RESULT, HookResult),
        )
        observe_to_think_binding = Graph.bind(ReActValueName.RESULT, think_result_input)
        think_request = think_phase.add_node(
            ReActPhaseNodeId.PROJECT,
            operations.project_observe_to_think,
            inputs=(observe_to_think_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(observe_to_think_binding),
                values.activation_config,
            ),
            output_name=ReActValueName.REQUEST,
            output_type=ThinkRequest,
        )
        think_phase.add_node(
            ReActPhaseNodeId.RUN,
            think,
            inputs={ReActValueName.REQUEST: think_request},
        )
        think_result = cast(
            NodeOutputRef[HookResult[ThinkFrame[ThinkStep, ThinkStateT], ThinkHookCommandT]],
            think_phase.output_ref(ReActPhaseNodeId.RUN, ReActValueName.RESULT),
        )
        think_result_binding = Graph.bind(
            ReActValueName.RESULT,
            Graph.node_output(think_result),
        )
        next_think_request = think_phase.add_node(
            ReActPhaseNodeId.NEXT_OBSERVE,
            operations.project_think_to_observe,
            inputs=(think_result_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(think_result_binding),
                values.activation_config,
            ),
            output_name=ReActValueName.REQUEST,
            output_type=ObserveRequest,
        )
        think_phase.add_edge(ReActPhaseNodeId.PROJECT, ReActPhaseNodeId.RUN)
        think_phase.add_edge(
            ReActPhaseNodeId.RUN,
            ThinkNodeId.COMMAND,
            ReActPhaseNodeId.NEXT_OBSERVE,
        )
        think_phase.add_edge(ReActPhaseNodeId.NEXT_OBSERVE, Graph.END)
        think_phase.set_outputs({ReActValueName.REQUEST: next_think_request})

        act_phase = Graph[HookGraphValue](f"{definition_id}.act-phase", version=version)
        act_result_input = cast(
            GraphInputRef[HookResult[ObserveHookEnvelope, ObserveHookCommandT]],
            Graph.graph_input(ReActValueName.RESULT, HookResult),
        )
        observe_to_act_binding = Graph.bind(ReActValueName.RESULT, act_result_input)
        act_request = act_phase.add_node(
            ReActPhaseNodeId.PROJECT,
            operations.project_observe_to_act,
            inputs=(observe_to_act_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(observe_to_act_binding),
                values.activation_config,
            ),
            output_name=ReActValueName.REQUEST,
            output_type=ActRequest,
        )
        act_phase.add_node(
            ReActPhaseNodeId.RUN,
            act,
            inputs={ReActValueName.REQUEST: act_request},
        )
        act_result = cast(
            NodeOutputRef[HookResult[ActHookEnvelope, ActHookCommandT]],
            act_phase.output_ref(ReActPhaseNodeId.RUN, ReActValueName.RESULT),
        )
        act_result_binding = Graph.bind(
            ReActValueName.RESULT,
            Graph.node_output(act_result),
        )
        next_act_request = act_phase.add_node(
            ReActPhaseNodeId.NEXT_OBSERVE,
            operations.project_act_to_observe,
            inputs=(act_result_binding,),
            input_type=ConfigActivation,
            materialize=lambda values: ConfigActivation(
                values.get(act_result_binding),
                values.activation_config,
            ),
            output_name=ReActValueName.REQUEST,
            output_type=ObserveRequest,
        )
        act_phase.add_edge(ReActPhaseNodeId.PROJECT, ReActPhaseNodeId.RUN)
        act_phase.add_edge(
            ReActPhaseNodeId.RUN,
            ActNodeId.SETTLE,
            ReActPhaseNodeId.NEXT_OBSERVE,
        )
        act_phase.add_edge(ReActPhaseNodeId.NEXT_OBSERVE, Graph.END)
        act_phase.set_outputs({ReActValueName.REQUEST: next_act_request})

        super().__init__(definition_id, version=version)
        self.add_node(
            ReActNodeId.OBSERVE,
            observe_phase,
            inputs={ReActValueName.REQUEST: Graph.node_output(ReActValueName.REQUEST)},
        )
        self.add_node(
            ReActNodeId.THINK,
            think_phase,
            inputs={ReActValueName.RESULT: Graph.node_output(observe_result)},
        )
        self.add_node(
            ReActNodeId.ACT,
            act_phase,
            inputs={ReActValueName.RESULT: Graph.node_output(observe_result)},
        )
        self.add_edge(Graph.START, ReActNodeId.OBSERVE)
        self.add_edge(ReActNodeId.THINK, ReActNodeId.OBSERVE)
        self.add_edge(ReActNodeId.ACT, ReActNodeId.OBSERVE)
        self.add_edge(ReActNodeId.OBSERVE, ReActRoute.CONFIG, Graph.END)
        self.add_edge(ReActNodeId.OBSERVE, ReActRoute.ASSISTANT, Graph.END)
        self.add_edge(ReActNodeId.OBSERVE, ReActRoute.THINK, ReActNodeId.ACT)
        self.add_edge(ReActNodeId.OBSERVE, ReActRoute.ACT, ReActNodeId.THINK)
        self.set_outputs(
            {
                ReActValueName.RESULT: self.output_ref(
                    ReActNodeId.OBSERVE,
                    ReActValueName.RESULT,
                )
            }
        )


__all__ = ["ReActNode"]

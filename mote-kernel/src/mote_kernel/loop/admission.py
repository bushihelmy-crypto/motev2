"""Exact boundary admission for the three graphs in a ReAct loop."""

import operator
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import (
    ActHookCommand,
    ActHookEnvelope,
    ActRequest,
    SettleStageValue,
)
from mote_kernel.act.contract import (
    HookStateProjection as ActHookStateProjection,
)
from mote_kernel.act.identity import ActHookStage
from mote_kernel.hooks.contract import HookGraphValue, HookResult
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
    ObserveResult,
    WriteObservationStageValue,
)
from mote_kernel.observe.identity import ObserveHookStage
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import CommandStep, ThinkFrame, ThinkRequest, ThinkStep

ObserveStateT = TypeVar("ObserveStateT", bound=ObserveHookStateProjection)
ObserveHookCommandT = TypeVar("ObserveHookCommandT", bound=ObserveHookCommand)
ThinkPayloadT = TypeVar("ThinkPayloadT")
ThinkStateT = TypeVar("ThinkStateT", bound=HookGraphValue)
ThinkHookCommandT = TypeVar("ThinkHookCommandT", bound=HookGraphValue)
ActStateT = TypeVar("ActStateT", bound=ActHookStateProjection)
ActHookCommandT = TypeVar("ActHookCommandT", bound=ActHookCommand)
_HookValueTypeT = TypeVar("_HookValueTypeT")


def _require_concrete_hook_value_type(value: type[_HookValueTypeT], field: str, /) -> None:
    try:
        valid = value is not HookGraphValue and issubclass(value, HookGraphValue)
    except TypeError as error:
        raise ReActContractError(f"{field} must be a concrete HookGraphValue class") from error
    if not valid:
        raise ReActContractError(f"{field} must be a concrete HookGraphValue class")


@dataclass(frozen=True, slots=True)
class ReActPayloadAdmission(
    Generic[
        ObserveStateT,
        ObserveHookCommandT,
        ThinkStateT,
        ThinkHookCommandT,
        ActStateT,
        ActHookCommandT,
    ]
):
    """Admit confirmed child boundaries and their projected requests."""

    observe: ObservePayloadAdmission
    think_state_type: type[ThinkStateT]
    think_hook_command_type: type[ThinkHookCommandT]
    act: ActPayloadAdmission[ActStateT, ActHookCommandT]

    def __post_init__(self) -> None:
        if type(self.observe) is not ObservePayloadAdmission:
            raise ReActContractError("ReAct requires the Observe payload admission")
        _require_concrete_hook_value_type(
            self.think_state_type,
            "ReAct Think state type",
        )
        _require_concrete_hook_value_type(
            self.think_hook_command_type,
            "ReAct Think command type",
        )
        if type(self.act) is not ActPayloadAdmission:
            raise ReActContractError("ReAct requires the Act payload admission")
        if not operator.is_(self.observe.hook_state_type, self.think_state_type) or not operator.is_(
            self.observe.hook_state_type,
            self.act.hook_state_type,
        ):
            raise ReActContractError("ReAct children must share one concrete Hook state type")

    def admit_observe_boundary(
        self,
        value: HookGraphValue,
        /,
    ) -> HookResult[ObserveHookEnvelope, ObserveHookCommandT]:
        if type(value) is not HookResult:
            raise ReActContractError("Observe completion must be an exact HookResult")
        boundary = cast(HookResult[ObserveHookEnvelope, ObserveHookCommandT], value)
        admitted = self.observe.admit_hook_result(boundary)
        envelope = admitted.value
        if (
            envelope.stage is not ObserveHookStage.AFTER_WRITE_OBSERVATION
            or type(envelope.payload) is not WriteObservationStageValue
            or admitted.node_id != GraphNodeId("write_observation")
        ):
            raise ReActContractError("Observe completion must be its final write_observation boundary")
        return admitted

    def observe_completion(
        self,
        value: HookResult[ObserveHookEnvelope, ObserveHookCommandT],
        /,
    ) -> tuple[ObserveResult, ObserveStateT]:
        boundary = self.admit_observe_boundary(value)
        payload = cast(WriteObservationStageValue, boundary.value.payload)
        result = self.observe.admit_result(payload.result)
        state = boundary.value.hook_state
        if type(state) is not self.observe.hook_state_type:
            raise ReActContractError("Observe completion has an unexpected concrete state type")
        return result, cast(ObserveStateT, state)

    def admit_think_boundary(
        self,
        value: HookGraphValue,
        /,
    ) -> HookResult[ThinkFrame[ThinkStep, ThinkStateT], ThinkHookCommandT]:
        if type(value) is not HookResult:
            raise ReActContractError("Think completion must be an exact HookResult")
        boundary = cast(
            HookResult[ThinkFrame[ThinkStep, ThinkStateT], ThinkHookCommandT],
            value,
        )
        frame = boundary.value
        if type(frame) is not ThinkFrame or type(frame.step) is not CommandStep:
            raise ReActContractError("Think completion must contain its final CommandStep")
        if type(frame.hook_state) is not self.think_state_type:
            raise ReActContractError("Think completion has an unexpected concrete state type")
        if type(boundary.commands) is not tuple or any(
            type(command) is not self.think_hook_command_type for command in boundary.commands
        ):
            raise ReActContractError("Think completion has unexpected Hook commands")
        if boundary.node_id != GraphNodeId("command"):
            raise ReActContractError("Think completion must be its final command boundary")
        return boundary

    def admit_act_boundary(
        self,
        value: HookGraphValue,
        /,
    ) -> HookResult[ActHookEnvelope, ActHookCommandT]:
        admitted = self.act.admit_hook_result(value)
        envelope = admitted.value
        if (
            envelope.stage is not ActHookStage.SETTLE
            or type(envelope.payload) is not SettleStageValue
            or admitted.node_id != GraphNodeId("settle")
        ):
            raise ReActContractError("Act completion must be its final settle boundary")
        return admitted

    def select_route(
        self,
        policy: ObserveRoutePolicy,
        value: HookResult[ObserveHookEnvelope, ObserveHookCommandT],
        /,
    ) -> ReActRoute:
        result, _state = self.observe_completion(value)
        selected = policy(result)
        if type(selected) is not ReActRoute:
            raise ReActContractError("Observe route policy must return a ReActRoute")
        return selected

    def project_observe_to_act(
        self,
        projector: ObserveToActProjector[ObserveHookCommandT],
        value: HookResult[ObserveHookEnvelope, ObserveHookCommandT],
        /,
    ) -> ActRequest:
        boundary = self.admit_observe_boundary(value)
        admitted = self.act.admit_request(projector(boundary))
        if type(admitted.hook_state) is not self.act.hook_state_type:
            raise ReActContractError("Observe-to-Act projection returned the wrong concrete state type")
        return admitted

    def project_observe_to_think(
        self,
        projector: ObserveToThinkProjector[
            ObserveHookCommandT,
            ThinkPayloadT,
            ThinkStateT,
        ],
        value: HookResult[ObserveHookEnvelope, ObserveHookCommandT],
        /,
    ) -> ThinkRequest[ThinkPayloadT, ThinkStateT]:
        boundary = self.admit_observe_boundary(value)
        projected = projector(boundary)
        if type(projected) is not ThinkRequest:
            raise ReActContractError("Observe-to-Think projection must return an exact ThinkRequest")
        if type(projected.hook_state) is not self.think_state_type:
            raise ReActContractError("Observe-to-Think projection returned the wrong concrete state type")
        return projected

    def project_think_to_observe(
        self,
        projector: ThinkToObserveProjector[
            ThinkStateT,
            ThinkHookCommandT,
            ObserveStateT,
        ],
        value: HookResult[ThinkFrame[ThinkStep, ThinkStateT], ThinkHookCommandT],
        /,
    ) -> ObserveRequest[ObserveStateT]:
        boundary = self.admit_think_boundary(value)
        return self.observe.admit_request(projector(boundary))

    def project_act_to_observe(
        self,
        projector: ActToObserveProjector[ActHookCommandT, ObserveStateT],
        value: HookResult[ActHookEnvelope, ActHookCommandT],
        /,
    ) -> ObserveRequest[ObserveStateT]:
        boundary = self.admit_act_boundary(value)
        return self.observe.admit_request(projector(boundary))


__all__: list[str] = []

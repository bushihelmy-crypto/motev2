"""Boundary and data-flow tests for the three-node ReAct composition."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from types import MethodType
from typing import Never, Protocol, cast

import pytest

from mote_kernel.act.admission import ActPayloadAdmission
from mote_kernel.act.contract import ActHookEnvelope, ActRequest
from mote_kernel.act.contract import HookStateProjection as ActHookStateProjection
from mote_kernel.act.node import ActNode
from mote_kernel.config import Config, ConfigActivation, ConfigContractError, ConfigSnapshotKey
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookPayloadAdmission, HookResult, HookTransitionAdmission
from mote_kernel.loop.admission import ReActPayloadAdmission
from mote_kernel.loop.config import ReActRouteBinding, ReActRuntimeConfig
from mote_kernel.loop.contract import (
    ActToObserveProjector,
    ObserveRoutePolicy,
    ObserveToActProjector,
    ObserveToThinkProjector,
    ReActContractError,
    ReActRoute,
    ThinkToObserveProjector,
)
from mote_kernel.loop.node import ReActNode
from mote_kernel.observe.admission import ObservePayloadAdmission
from mote_kernel.observe.contract import (
    BackgroundTaskSnapshot,
    GetObservationStageValue,
    ObserveFrame,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.contract import (
    HookStateProjection as ObserveStateProjection,
)
from mote_kernel.observe.identity import ObserveHookStage
from mote_kernel.observe.node import ObserveNode
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId
from mote_kernel.think.contract import ThinkFrame, ThinkRequest, ThinkStep
from mote_kernel.think.node import ThinkNode

from .support import (
    ActCommand,
    ActPorts,
    ObservationText,
    ObserveCommand,
    ObservePorts,
    Priority,
    SharedState,
    ThinkCommand,
    ThinkPayload,
    ThinkPorts,
    act_admission,
    available,
    delivery,
    make_act_node,
    make_observe_node,
    make_think_node,
    next_state,
    react_admission,
    valid_act_boundary,
    valid_act_request,
    valid_observe_boundary,
    valid_observe_result,
    valid_think_boundary,
)


@dataclass(frozen=True, slots=True)
class _OtherObserveState(ObserveStateProjection):
    marker: str = "other-observe"


@dataclass(frozen=True, slots=True)
class _OtherActState(ActHookStateProjection):
    marker: str = "other-act"


@dataclass(frozen=True, slots=True)
class _OtherCommand(HookGraphValue):
    marker: str = "other-command"


class _LoopBuilderState(Protocol):
    nodes: tuple[object, ...]


class _InspectableLoopGraph(Protocol):
    _builder_state: _LoopBuilderState


class _NestedLoopNode(Protocol):
    graph: Graph[HookGraphValue]


class _CallableLoopNode(Protocol):
    invoker: object


class _TypedOperationInvoker(Protocol):
    operation: object


class _ReActOperationsView(Protocol):
    async def project_act_to_observe(
        self,
        activation: ConfigActivation[HookResult[ActHookEnvelope, ActCommand]],
        /,
    ) -> ObserveRequest[SharedState]: ...


class _ReActOperationsPrivateView(_ReActOperationsView, Protocol):
    def _admit_runtime_identity(self, selected: ReActRuntimeConfig, /) -> None: ...

    def _bind_runtime_capability(
        self,
        config: Config | None,
        selector: object,
        fallback: object,
        /,
    ) -> object: ...

    def _bind_prepare_identity(self, config: Config | None, /) -> None: ...


def _get_observation_boundary() -> HookResult[ObserveHookEnvelope, ObserveCommand]:
    read = available(delivery(0, UserObservation(ObservationText("hello")), "delivery-0"))
    frame = ObserveFrame(
        read.batch,
        read.boundary,
        BackgroundTaskSnapshot(read.boundary.observation_revision, ()),
    )
    envelope = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(frame),
        SharedState(),
    )
    return HookResult(envelope, (), GraphNodeId("get_observation"))


def _forged(value: object, field: str, replacement: object) -> object:
    """Copy a frozen slots DTO while bypassing its constructor for ingress tests."""

    clone = copy(value)
    object.__setattr__(clone, field, replacement)
    return clone


def test_package_surface_is_only_react_node() -> None:
    import mote_kernel.loop as loop_package
    import mote_kernel.loop.config as loop_config

    assert loop_package.__all__ == ["ReActNode"]
    assert loop_package.ReActNode is ReActNode
    assert not hasattr(loop_package, "ReActRoute")
    assert loop_config.__all__ == []


def test_observe_admission_rejects_non_final_stage_and_wrong_producer() -> None:
    admission = react_admission()
    with pytest.raises(ValueError, match="final write_observation"):
        admission.admit_observe_boundary(_get_observation_boundary())

    wrong_node = replace(valid_observe_boundary(), node_id=GraphNodeId("get_observation"))
    with pytest.raises(ValueError, match="node_id does not match"):
        admission.admit_observe_boundary(wrong_node)


@pytest.mark.parametrize(
    "value",
    [None, Graph.values(), _OtherCommand(), object()],
    ids=["none", "graph-values", "wrong-command", "plain-object"],
)
def test_observe_admission_rejects_wrong_outer_boundary(value: object) -> None:
    with pytest.raises(ValueError):
        react_admission().admit_observe_boundary(cast(HookGraphValue, value))


def test_observe_admission_revalidates_nested_result_evidence() -> None:
    boundary = valid_observe_boundary()
    result = cast(WriteObservationStageValue, boundary.value.payload).result
    forged = _forged(result, "current_state", result.current_state.__class__.CONFIG)
    forged_envelope = replace(
        boundary.value,
        payload=WriteObservationStageValue(cast(ObserveResult, forged)),
    )
    forged_boundary = HookResult(forged_envelope, boundary.commands, boundary.node_id)

    with pytest.raises(ValueError, match="current_state does not match"):
        react_admission().admit_observe_boundary(forged_boundary)


@pytest.mark.parametrize("route", list(ReActRoute))
def test_route_policy_accepts_every_closed_route(route: ReActRoute) -> None:
    seen: list[ObserveResult] = []

    def policy(result: ObserveResult) -> ReActRoute:
        seen.append(result)
        return route

    admission = react_admission()
    assert admission.select_route(policy, valid_observe_boundary()) is route
    assert seen == [valid_observe_result()]


def test_route_policy_is_fail_closed_for_unknown_values() -> None:
    def invalid_policy(_result: ObserveResult) -> ReActRoute:
        return cast(ReActRoute, "think")

    with pytest.raises(ReActContractError, match="must return a ReActRoute"):
        react_admission().select_route(invalid_policy, valid_observe_boundary())


def test_route_policy_is_not_called_for_an_invalid_boundary() -> None:
    calls = 0

    def policy(_result: ObserveResult) -> ReActRoute:
        nonlocal calls
        calls += 1
        return ReActRoute.CONFIG

    with pytest.raises(ValueError):
        react_admission().select_route(policy, _get_observation_boundary())
    assert calls == 0


def test_observe_to_think_projection_preserves_confirmed_state() -> None:
    boundary = valid_observe_boundary()
    expected_state = next_state(boundary)
    projected = react_admission().project_observe_to_think(
        lambda _value: ThinkRequest(ThinkPayload("prompt"), expected_state),
        boundary,
    )
    assert type(projected) is ThinkRequest
    assert projected.payload == ThinkPayload("prompt")
    assert projected.hook_state is expected_state


def test_observe_to_act_projection_uses_the_act_request_contract() -> None:
    boundary = valid_observe_boundary()
    expected_state = next_state(boundary)
    projected = react_admission().project_observe_to_act(
        lambda _value: _act_request_with_state(expected_state),
        boundary,
    )
    assert type(projected) is ActRequest
    assert projected.hook_state is expected_state


def _act_request_with_state(state: SharedState) -> ActRequest:
    return valid_act_request(state)


def test_observe_projectors_reject_wrong_request_types_and_states() -> None:
    boundary = valid_observe_boundary()
    admission = react_admission()

    def invalid_think_projector(
        _value: HookResult[ObserveHookEnvelope, ObserveCommand],
    ) -> ThinkRequest[ThinkPayload, SharedState]:
        return cast(ThinkRequest[ThinkPayload, SharedState], object())

    def invalid_act_projector(
        _value: HookResult[ObserveHookEnvelope, ObserveCommand],
    ) -> ActRequest:
        return cast(ActRequest, object())

    with pytest.raises(ValueError):
        admission.project_observe_to_think(invalid_think_projector, boundary)
    with pytest.raises(ValueError):
        admission.project_observe_to_act(invalid_act_projector, boundary)

    with pytest.raises(ValueError, match="wrong concrete state"):
        admission.project_observe_to_think(
            lambda _value: ThinkRequest(ThinkPayload("prompt"), cast(SharedState, _OtherObserveState())),
            boundary,
        )


def test_think_completion_projection_requires_the_final_command_boundary() -> None:
    admission = react_admission()
    projected = admission.project_think_to_observe(
        lambda _value: ObserveRequest(SharedState().cursor, SharedState()),
        valid_think_boundary(),
    )
    assert type(projected) is ObserveRequest

    wrong_node = replace(valid_think_boundary(), node_id=GraphNodeId("inference"))
    with pytest.raises(ValueError, match=r"final command boundary|node_id"):
        admission.project_think_to_observe(
            lambda _value: ObserveRequest(SharedState().cursor, SharedState()),
            wrong_node,
        )


def test_act_completion_projection_requires_the_final_settle_boundary() -> None:
    admission = react_admission()
    projected = admission.project_act_to_observe(
        lambda _value: ObserveRequest(SharedState().cursor, SharedState()),
        valid_act_boundary(),
    )
    assert type(projected) is ObserveRequest

    wrong_node = replace(valid_act_boundary(), node_id=GraphNodeId("execute"))
    with pytest.raises(ValueError, match="node_id does not match"):
        admission.project_act_to_observe(
            lambda _value: ObserveRequest(SharedState().cursor, SharedState()),
            wrong_node,
        )


def test_think_boundary_rejects_wrong_step_state_command_and_node() -> None:
    admission = react_admission()
    valid = valid_think_boundary()
    with pytest.raises(ReActContractError, match="CommandStep"):
        admission.admit_think_boundary(cast(HookGraphValue, _forged(valid, "value", object())))

    wrong_state = _forged(valid.value, "hook_state", _OtherObserveState())
    with pytest.raises(ReActContractError, match="concrete state"):
        admission.admit_think_boundary(
            HookResult(cast(ThinkFrame[ThinkStep, SharedState], wrong_state), valid.commands, valid.node_id)
        )

    wrong_commands = HookResult(valid.value, (_OtherCommand(),), valid.node_id)
    with pytest.raises(ReActContractError, match="Hook commands"):
        admission.admit_think_boundary(wrong_commands)

    wrong_node = replace(valid, node_id=GraphNodeId("inference"))
    with pytest.raises(ReActContractError, match="final command boundary"):
        admission.admit_think_boundary(wrong_node)


def test_act_boundary_delegates_deep_admission_and_checks_settle_identity() -> None:
    admission = react_admission()
    valid = valid_act_boundary()
    assert admission.admit_act_boundary(valid) is valid

    wrong_commands = HookResult(valid.value, (_OtherCommand(),), valid.node_id)
    with pytest.raises(ValueError, match="unexpected concrete type"):
        admission.admit_act_boundary(wrong_commands)

    wrong_node = replace(valid, node_id=GraphNodeId("execute"))
    with pytest.raises(ValueError, match="node_id does not match"):
        admission.admit_act_boundary(wrong_node)


def test_react_admission_requires_one_identical_concrete_state_type() -> None:
    with pytest.raises(ReActContractError, match="share one concrete Hook state type"):
        ReActPayloadAdmission(
            react_admission().observe,
            cast(type[SharedState], _OtherObserveState),
            ThinkCommand,
            act_admission(),
        )


def test_react_admission_rejects_nonconcrete_think_types() -> None:
    base = react_admission()
    with pytest.raises(ReActContractError, match="concrete HookGraphValue"):
        ReActPayloadAdmission(base.observe, HookGraphValue, ThinkCommand, base.act)

    with pytest.raises(ReActContractError, match="concrete HookGraphValue"):
        ReActPayloadAdmission(
            base.observe,
            cast(type[SharedState], object()),
            ThinkCommand,
            base.act,
        )


def test_react_admission_requires_exact_child_admission_owners() -> None:
    base = react_admission()
    with pytest.raises(ReActContractError, match="Observe payload admission"):
        ReActPayloadAdmission(
            cast(ObservePayloadAdmission, object()),
            SharedState,
            ThinkCommand,
            base.act,
        )
    with pytest.raises(ReActContractError, match="Act payload admission"):
        ReActPayloadAdmission(
            base.observe,
            SharedState,
            ThinkCommand,
            cast(ActPayloadAdmission[SharedState, ActCommand], object()),
        )


def test_react_admission_rejects_wrong_think_wrapper_and_projection_results() -> None:
    admission = react_admission()
    with pytest.raises(ReActContractError, match="exact HookResult"):
        admission.admit_think_boundary(cast(HookGraphValue, object()))

    with pytest.raises(ReActContractError, match="exact ObserveRequest"):
        admission.project_think_to_observe(
            lambda _value: cast(ObserveRequest[SharedState], object()),
            valid_think_boundary(),
        )
    with pytest.raises(ReActContractError, match="exact ObserveRequest"):
        admission.project_act_to_observe(
            lambda _value: cast(ObserveRequest[SharedState], object()),
            valid_act_boundary(),
        )


def test_react_admission_rejects_wrong_act_boundary_and_projected_state(monkeypatch: pytest.MonkeyPatch) -> None:
    admission = react_admission()
    boundary = valid_act_boundary()
    malformed = _forged(boundary.value, "stage", cast(object, None))
    malformed_boundary = HookResult[ActHookEnvelope, ActCommand](
        cast(ActHookEnvelope, malformed),
        boundary.commands,
        boundary.node_id,
    )

    def passthrough_hook_result(
        _self: ActPayloadAdmission[SharedState, ActCommand],
        value: HookGraphValue,
    ) -> HookGraphValue:
        return value

    monkeypatch.setattr(ActPayloadAdmission, "admit_hook_result", passthrough_hook_result)
    with pytest.raises(ReActContractError, match="final settle boundary"):
        admission.admit_act_boundary(malformed_boundary)

    observe = valid_observe_boundary()
    monkeypatch.setattr(ObservePayloadAdmission, "admit_hook_result", passthrough_hook_result)
    wrong_state = _forged(observe.value, "hook_state", _OtherObserveState())
    wrong_boundary = HookResult[ObserveHookEnvelope, ObserveCommand](
        cast(ObserveHookEnvelope, wrong_state),
        observe.commands,
        observe.node_id,
    )
    with pytest.raises(ReActContractError, match="unexpected concrete state"):
        admission.observe_completion(wrong_boundary)

    def passthrough_act_request(
        _self: ActPayloadAdmission[SharedState, ActCommand],
        value: ActRequest,
    ) -> ActRequest:
        return value

    monkeypatch.setattr(ActPayloadAdmission, "admit_request", passthrough_act_request)
    with pytest.raises(ReActContractError, match="wrong concrete state"):
        admission.project_observe_to_act(
            lambda _value: valid_act_request(cast(SharedState, _OtherActState())),
            valid_observe_boundary(),
        )


def test_valid_state_and_projection_are_not_copied_or_reinterpreted() -> None:
    boundary = valid_observe_boundary()
    state = next_state(boundary)
    request = react_admission().project_observe_to_think(
        lambda _value: ThinkRequest(ThinkPayload("x"), state),
        boundary,
    )
    assert request.hook_state is state
    assert state.cursor == valid_observe_result().cursor_range.after


def test_admission_rejects_wrong_projector_result_without_invoking_downstream_logic() -> None:
    calls = 0

    def projector(
        _value: HookResult[ObserveHookEnvelope, ObserveCommand],
    ) -> ThinkRequest[ThinkPayload, SharedState]:
        nonlocal calls
        calls += 1
        return cast(ThinkRequest[ThinkPayload, SharedState], object())

    with pytest.raises(ValueError):
        react_admission().project_observe_to_think(projector, valid_observe_boundary())
    assert calls == 1


def test_public_react_constructor_rejects_wrong_child_types_before_graph_mutation() -> None:
    with pytest.raises(ReActContractError, match="ObserveNode"):
        ReActNode(
            "loop.invalid",
            observe=cast(ObserveNode[object, SharedState, ObserveCommand], object()),
            think=cast(ThinkNode[object, SharedState, ThinkCommand], object()),
            act=cast(ActNode[object, SharedState, ActCommand], object()),
            route_policy=cast(ObserveRoutePolicy, _act_request_with_state),
            observe_to_act=cast(ObserveToActProjector[ObserveCommand], _act_request_with_state),
            observe_to_think=cast(
                ObserveToThinkProjector[ObserveCommand, ThinkPayload, SharedState],
                _act_request_with_state,
            ),
            think_to_observe=cast(
                ThinkToObserveProjector[SharedState, ThinkCommand, SharedState],
                _act_request_with_state,
            ),
            act_to_observe=cast(ActToObserveProjector[ActCommand, SharedState], _act_request_with_state),
        )


def test_public_react_constructor_rejects_noncallable_policy_before_accessing_children() -> None:
    with pytest.raises(ReActContractError, match="ObserveNode"):
        ReActNode(
            "loop.invalid",
            observe=cast(ObserveNode[object, SharedState, ObserveCommand], object()),
            think=cast(ThinkNode[object, SharedState, ThinkCommand], object()),
            act=cast(ActNode[object, SharedState, ActCommand], object()),
            route_policy=cast(ObserveRoutePolicy, None),
            observe_to_act=cast(ObserveToActProjector[ObserveCommand], None),
            observe_to_think=cast(
                ObserveToThinkProjector[ObserveCommand, ThinkPayload, SharedState],
                None,
            ),
            think_to_observe=cast(
                ThinkToObserveProjector[SharedState, ThinkCommand, SharedState],
                None,
            ),
            act_to_observe=cast(ActToObserveProjector[ActCommand, SharedState], None),
        )


def _valid_react_children() -> tuple[
    ObserveNode[Priority, SharedState, ObserveCommand],
    ThinkNode[Priority, SharedState, ThinkCommand],
    ActNode[Priority, SharedState, ActCommand],
]:
    return (
        make_observe_node(ObservePorts()),
        make_think_node(ThinkPorts()),
        make_act_node(ActPorts()),
    )


def _construct_react_with(
    observe: object,
    think: object,
    act: object,
    route_policy: object,
    observe_to_act: object,
    observe_to_think: object,
    think_to_observe: object,
    act_to_observe: object,
) -> object:
    return ReActNode(
        "loop.invalid",
        observe=cast(ObserveNode[Priority, SharedState, ObserveCommand], observe),
        think=cast(ThinkNode[Priority, SharedState, ThinkCommand], think),
        act=cast(ActNode[Priority, SharedState, ActCommand], act),
        route_policy=cast(ObserveRoutePolicy, route_policy),
        observe_to_act=cast(ObserveToActProjector[ObserveCommand], observe_to_act),
        observe_to_think=cast(
            ObserveToThinkProjector[ObserveCommand, ThinkPayload, SharedState],
            observe_to_think,
        ),
        think_to_observe=cast(
            ThinkToObserveProjector[SharedState, ThinkCommand, SharedState],
            think_to_observe,
        ),
        act_to_observe=cast(ActToObserveProjector[ActCommand, SharedState], act_to_observe),
    )


def test_react_constructor_checks_each_child_and_projector_contract() -> None:
    def valid_route(_result: object) -> ReActRoute:
        return ReActRoute.CONFIG

    def valid_projector(_value: object) -> object:
        return object()

    cases: tuple[tuple[str, object], ...] = (
        ("think", object()),
        ("act", object()),
        ("route", None),
        ("observe_to_act", None),
        ("observe_to_think", None),
        ("think_to_observe", None),
        ("act_to_observe", None),
    )
    for field, invalid in cases:
        observe, think, act = _valid_react_children()
        values: dict[str, object] = {
            "observe": observe,
            "think": think,
            "act": act,
            "route": valid_route,
            "observe_to_act": valid_projector,
            "observe_to_think": valid_projector,
            "think_to_observe": valid_projector,
            "act_to_observe": valid_projector,
        }
        values[field] = invalid
        message = {
            "think": "ThinkNode",
            "act": "ActNode",
            "route": "route policy",
            "observe_to_act": "Observe-to-Act",
            "observe_to_think": "Observe-to-Think",
            "think_to_observe": "Think-to-Observe",
            "act_to_observe": "Act-to-Observe",
        }[field]
        with pytest.raises(ReActContractError, match=message):
            _construct_react_with(
                values["observe"],
                values["think"],
                values["act"],
                values["route"],
                values["observe_to_act"],
                values["observe_to_think"],
                values["think_to_observe"],
                values["act_to_observe"],
            )


@pytest.mark.parametrize(
    "fault",
    (
        "observe-admission",
        "observe-value",
        "observe-state",
        "observe-command",
        "observe-transition",
        "think-admission",
        "think-value",
        "act-admission",
        "act-value",
        "act-state",
        "act-command",
        "act-transition",
    ),
)
def test_react_constructor_checks_each_shared_hook_admission_contract(fault: str) -> None:
    observe, think, act = _valid_react_children()

    def valid_projector(_value: object) -> object:
        return object()

    if fault == "observe-admission":
        object.__setattr__(observe.hook, "_payload_admission", cast(Never, object()))
    elif fault == "observe-value":
        object.__setattr__(
            observe.hook,
            "_payload_admission",
            HookPayloadAdmission(
                int,
                ActHookEnvelope,
                SharedState,
                ObserveCommand,
                None,
            ),
        )
    elif fault == "observe-state":
        original = observe.hook.payload_admission
        object.__setattr__(
            observe.hook,
            "_payload_admission",
            HookPayloadAdmission(
                original.priority_config_type,
                original.value_type,
                _OtherObserveState,
                original.command_type,
                cast(
                    HookTransitionAdmission[ObserveHookEnvelope, _OtherObserveState, ObserveCommand],
                    original.transition_admission,
                ),
            ),
        )
    elif fault == "observe-command":
        original = observe.hook.payload_admission
        object.__setattr__(
            observe.hook,
            "_payload_admission",
            HookPayloadAdmission(
                original.priority_config_type,
                original.value_type,
                original.state_type,
                _OtherCommand,
                cast(
                    HookTransitionAdmission[ObserveHookEnvelope, SharedState, _OtherCommand],
                    original.transition_admission,
                ),
            ),
        )
    elif fault == "observe-transition":
        object.__setattr__(observe.hook.payload_admission, "transition_admission", cast(Never, object()))
    elif fault == "think-admission":
        object.__setattr__(think.hook, "_payload_admission", cast(Never, object()))
    elif fault == "think-value":
        original = think.hook.payload_admission
        object.__setattr__(
            think.hook,
            "_payload_admission",
            HookPayloadAdmission(
                original.priority_config_type,
                ActHookEnvelope,
                original.state_type,
                original.command_type,
                None,
            ),
        )
    elif fault == "act-admission":
        object.__setattr__(act.hook, "_payload_admission", cast(Never, object()))
    elif fault == "act-value":
        original = act.hook.payload_admission
        object.__setattr__(
            act.hook,
            "_payload_admission",
            HookPayloadAdmission(
                original.priority_config_type,
                ObserveHookEnvelope,
                original.state_type,
                original.command_type,
                None,
            ),
        )
    elif fault == "act-state":
        original = act.hook.payload_admission
        object.__setattr__(
            act.hook,
            "_payload_admission",
            HookPayloadAdmission(
                original.priority_config_type,
                original.value_type,
                _OtherActState,
                original.command_type,
                cast(
                    HookTransitionAdmission[ActHookEnvelope, _OtherActState, ActCommand],
                    original.transition_admission,
                ),
            ),
        )
    elif fault == "act-command":
        original = act.hook.payload_admission
        object.__setattr__(
            act.hook,
            "_payload_admission",
            HookPayloadAdmission(
                original.priority_config_type,
                original.value_type,
                original.state_type,
                _OtherCommand,
                cast(
                    HookTransitionAdmission[ActHookEnvelope, SharedState, _OtherCommand],
                    original.transition_admission,
                ),
            ),
        )
    else:
        object.__setattr__(act.hook.payload_admission, "transition_admission", cast(Never, object()))

    messages = {
        "observe-admission": "ObserveNode must expose a HookPayloadAdmission",
        "observe-value": "ObserveNode Hook must carry",
        "observe-state": "ObserveNode Hook state type",
        "observe-command": "ObserveNode Hook command type",
        "observe-transition": "Observe payload admission",
        "think-admission": "ThinkNode must expose a HookPayloadAdmission",
        "think-value": "ThinkNode must expose a ThinkFrame",
        "act-admission": "ActNode must expose a HookPayloadAdmission",
        "act-value": "ActNode Hook must carry",
        "act-state": "ActNode Hook state type",
        "act-command": "ActNode Hook command type",
        "act-transition": "Act payload admission",
    }
    with pytest.raises(ReActContractError, match=messages[fault]):
        _construct_react_with(
            observe,
            think,
            act,
            _route_policy,
            valid_projector,
            valid_projector,
            valid_projector,
            valid_projector,
        )


def _route_policy(_result: object) -> ReActRoute:
    return ReActRoute.CONFIG


def _observe_to_act(_value: object) -> ActRequest:
    return valid_act_request()


def _observe_to_think(_value: object) -> ThinkRequest[ThinkPayload, SharedState]:
    return ThinkRequest(ThinkPayload("think"), SharedState())


def _think_to_observe(_value: object) -> ObserveRequest[SharedState]:
    return ObserveRequest(SharedState().cursor, SharedState())


def _act_to_observe(_value: object) -> ObserveRequest[SharedState]:
    return ObserveRequest(SharedState().cursor, SharedState())


def _operations() -> _ReActOperationsView:
    observe, think, act = _valid_react_children()
    react = ReActNode(
        "loop.probe",
        observe=observe,
        think=think,
        act=act,
        route_policy=cast(ObserveRoutePolicy, _route_policy),
        observe_to_act=cast(ObserveToActProjector[ObserveCommand], _observe_to_act),
        observe_to_think=cast(
            ObserveToThinkProjector[ObserveCommand, ThinkPayload, SharedState],
            _observe_to_think,
        ),
        think_to_observe=cast(
            ThinkToObserveProjector[SharedState, ThinkCommand, SharedState],
            _think_to_observe,
        ),
        act_to_observe=cast(ActToObserveProjector[ActCommand, SharedState], _act_to_observe),
    )
    root = cast(_InspectableLoopGraph, react)
    root_state = cast(_LoopBuilderState, object.__getattribute__(root, "_builder_state"))
    observe_phase = cast(_NestedLoopNode, root_state.nodes[0]).graph
    phase = cast(_InspectableLoopGraph, observe_phase)
    phase_state = cast(_LoopBuilderState, object.__getattribute__(phase, "_builder_state"))
    prepare = cast(_CallableLoopNode, phase_state.nodes[0])
    invoker = cast(_TypedOperationInvoker, prepare.invoker)
    operation = cast(MethodType, invoker.operation)
    return cast(_ReActOperationsView, operation.__self__)


def _admit_runtime_identity(operations: object, selected: ReActRuntimeConfig, /) -> None:
    object.__getattribute__(
        cast(_ReActOperationsPrivateView, operations),
        "_admit_runtime_identity",
    )(selected)


def _bind_runtime_capability(
    operations: object,
    config: Config | None,
    selector: object,
    fallback: object,
    /,
) -> object:
    return object.__getattribute__(
        cast(_ReActOperationsPrivateView, operations),
        "_bind_runtime_capability",
    )(config, selector, fallback)


def _bind_prepare_identity(operations: object, config: Config | None, /) -> None:
    object.__getattribute__(
        cast(_ReActOperationsPrivateView, operations),
        "_bind_prepare_identity",
    )(config)


class _BindingFailureConfig:
    def __init__(self, error: ConfigContractError | None = None, result: object | None = None) -> None:
        self.error = error
        self.result = result

    def bind(self, _selector: object, /) -> object:
        if self.error is not None:
            raise self.error
        return self.result


def test_react_operation_runtime_and_projection_boundaries_are_explicit() -> None:
    operations = _operations()
    snapshot_key = ConfigSnapshotKey(
        GraphDefinitionId("loop"),
        GraphDefinitionVersion(1),
        1,
    )

    with pytest.raises(ReActContractError, match="invalid projection"):
        _admit_runtime_identity(operations, cast(ReActRuntimeConfig, object()))

    mismatched = ReActRuntimeConfig(
        snapshot_key,
        GraphDefinitionId("other-loop"),
        GraphDefinitionVersion(1),
    )
    with pytest.raises(ReActContractError, match="compiled graph contract"):
        _admit_runtime_identity(operations, mismatched)

    failing = cast(Config, _BindingFailureConfig(ConfigContractError("selector failed")))
    with pytest.raises(ReActContractError, match="selector failed"):
        _bind_runtime_capability(
            operations,
            failing,
            ReActRouteBinding(),
            cast(ObserveRoutePolicy, _route_policy),
        )
    invalid = cast(
        Config,
        _BindingFailureConfig(
            result=ReActRuntimeConfig(
                snapshot_key,
                GraphDefinitionId("loop"),
                GraphDefinitionVersion(1),
            )
        ),
    )
    with pytest.raises(ReActContractError, match="invalid projection"):
        _bind_runtime_capability(
            operations,
            invalid,
            ReActRouteBinding(),
            cast(ObserveRoutePolicy, _route_policy),
        )

    with pytest.raises(ReActContractError, match="selector failed"):
        _bind_prepare_identity(operations, failing)


@pytest.mark.asyncio
async def test_react_projects_act_completion_back_to_observe() -> None:
    operations = _operations()
    activation = ConfigActivation(valid_act_boundary())
    projected = await operations.project_act_to_observe(activation)
    assert type(projected) is ObserveRequest

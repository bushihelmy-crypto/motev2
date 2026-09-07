"""Boundary and data-flow tests for the three-node ReAct composition."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

import pytest

from mote_kernel.act.contract import ActRequest
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookGraphValue, HookResult
from mote_kernel.loop.admission import ReActPayloadAdmission
from mote_kernel.loop.contract import ReActContractError, ReActRoute
from mote_kernel.loop.node import ReActNode
from mote_kernel.observe.contract import (
    GetObservationStageValue,
    ObserveFrame,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    WriteObservationStageValue,
)
from mote_kernel.observe.contract import (
    HookStateProjection as ObserveStateProjection,
)
from mote_kernel.observe.identity import ObserveHookStage
from mote_kernel.state.graph_state import GraphNodeId
from mote_kernel.think.contract import ThinkFrame, ThinkRequest, ThinkStep

from .support import (
    SharedState,
    ThinkCommand,
    ThinkPayload,
    act_admission,
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
class _OtherCommand(HookGraphValue):
    marker: str = "other-command"


def _get_observation_boundary() -> HookResult[ObserveHookEnvelope, object]:
    result = valid_observe_result()
    read_boundary = result.observation_receipt.read_boundary
    frame = ObserveFrame(result.batch, read_boundary, result.background_task_snapshot)
    envelope = ObserveHookEnvelope(
        ObserveHookStage.AFTER_GET_OBSERVATION,
        GetObservationStageValue(frame),
        SharedState(),
    )
    return HookResult(envelope, (), GraphNodeId("get_observation"))


def _forged(value: object, field: str, replacement: object) -> object:
    """Copy a frozen slots DTO while bypassing its constructor for ingress tests."""

    cls = type(value)
    clone = object.__new__(cls)
    for slot in cls.__slots__:
        object.__setattr__(clone, slot, getattr(value, slot))
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
    with pytest.raises(ReActContractError, match="must return a ReActRoute"):
        react_admission().select_route(cast(object, lambda _result: "think"), valid_observe_boundary())


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

    with pytest.raises(ValueError):
        admission.project_observe_to_think(cast(object, lambda _value: object()), boundary)
    with pytest.raises(ValueError):
        admission.project_observe_to_act(cast(object, lambda _value: object()), boundary)

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
        admission.admit_think_boundary(_forged(valid, "value", object()))  # type: ignore[arg-type]

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

    def projector(_value: HookResult[ObserveHookEnvelope, object]) -> ThinkRequest[ThinkPayload, SharedState]:
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
            observe=cast(object, object()),
            think=cast(object, object()),
            act=cast(object, object()),
            route_policy=cast(object, lambda _result: ReActRoute.CONFIG),
            observe_to_act=cast(object, lambda _result: None),
            observe_to_think=cast(object, lambda _result: None),
            think_to_observe=cast(object, lambda _result: None),
            act_to_observe=cast(object, lambda _result: None),
        )


def test_public_react_constructor_rejects_noncallable_policy_before_accessing_children() -> None:
    with pytest.raises(ReActContractError, match="ObserveNode"):
        ReActNode(
            "loop.invalid",
            observe=cast(object, object()),
            think=cast(object, object()),
            act=cast(object, object()),
            route_policy=cast(object, None),
            observe_to_act=cast(object, None),
            observe_to_think=cast(object, None),
            think_to_observe=cast(object, None),
            act_to_observe=cast(object, None),
        )

"""End-to-end ReAct routing tests through the canonical Graph executor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from mote_kernel.act.contract import ActHookEnvelope, Allow
from mote_kernel.act.node import ActNode
from mote_kernel.execution import Graph
from mote_kernel.hooks.contract import HookResult
from mote_kernel.loop.contract import ReActRoute
from mote_kernel.loop.node import ReActNode
from mote_kernel.observe.contract import (
    AssistantObservation,
    ConfigObservation,
    ObserveHookEnvelope,
    ObserveRequest,
    ObserveResult,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.identity import ObservationCursor
from mote_kernel.think.contract import (
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)

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
    available,
    cursor,
    delivery,
    empty,
    make_act_node,
    make_observe_node,
    make_think_node,
    valid_act_request,
)

RoutePolicy = Callable[[ObserveResult], ReActRoute]


def _react(
    observe_ports: ObservePorts,
    *,
    think_ports: ThinkPorts | None = None,
    act_ports: ActPorts | None = None,
    policy: RoutePolicy,
) -> tuple[
    ReActNode[SharedState, ThinkPayload, SharedState, SharedState],
    ThinkPorts,
    ActPorts,
    ActNode[Priority, SharedState, ActCommand],
]:
    actual_think_ports = ThinkPorts() if think_ports is None else think_ports
    actual_act_ports = ActPorts() if act_ports is None else act_ports
    observe = make_observe_node(observe_ports)
    think = make_think_node(actual_think_ports)
    act = make_act_node(actual_act_ports)

    def observe_to_think(
        value: HookResult[ObserveHookEnvelope, ObserveCommand],
        /,
    ) -> ThinkRequest[ThinkPayload, SharedState]:
        result = cast(WriteObservationStageValue, value.value.payload).result
        state = replace(cast(SharedState, value.value.hook_state), cursor=result.cursor_range.after)
        return ThinkRequest(ThinkPayload("think"), state)

    def observe_to_act(
        value: HookResult[ObserveHookEnvelope, ObserveCommand],
        /,
    ):
        result = cast(WriteObservationStageValue, value.value.payload).result
        state = replace(cast(SharedState, value.value.hook_state), cursor=result.cursor_range.after)
        return valid_act_request(state)

    def think_to_observe(
        value: HookResult[ThinkFrame[ThinkStep, SharedState], ThinkCommand],
        /,
    ) -> ObserveRequest[SharedState]:
        state = value.value.hook_state
        return ObserveRequest(state.cursor, state)

    def act_to_observe(
        value: HookResult[ActHookEnvelope, ActCommand],
        /,
    ) -> ObserveRequest[SharedState]:
        # Act admission guarantees the concrete envelope and state before
        # this projector is called; the test keeps the projector pure.
        state = cast(SharedState, value.value.hook_state)
        return ObserveRequest(state.cursor, state)

    node = ReActNode(
        "loop.integration",
        observe=observe,
        think=think,
        act=act,
        route_policy=policy,
        observe_to_act=observe_to_act,
        observe_to_think=observe_to_think,
        think_to_observe=think_to_observe,
        act_to_observe=act_to_observe,
    )
    return node, actual_think_ports, actual_act_ports, act


def _request(sequence: int = 0) -> ObserveRequest[SharedState]:
    return ObserveRequest(ObservationCursor("stream", sequence), SharedState(cursor=cursor(sequence)))


@pytest.mark.asyncio
async def test_config_observation_finishes_at_the_terminal_parent_boundary() -> None:
    ports = ObservePorts((available(delivery(0, ConfigObservation(ObservationText("config")), "config-1")),))
    node, _think_ports, _act_ports, _act_node = _react(ports, policy=lambda _result: ReActRoute.CONFIG)

    result = await node.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    assert result.state.completion_route == ReActRoute.CONFIG.value
    output = result.outputs["result"]
    assert type(output) is HookResult
    assert str(cast(HookResult[ObserveHookEnvelope, ObserveCommand], output).node_id) == "write_observation"
    assert ports.read_cursors == [cursor(0)]


@pytest.mark.asyncio
async def test_assistant_observation_uses_the_other_terminal_route() -> None:
    ports = ObservePorts((available(delivery(0, AssistantObservation(ObservationText("answer")), "assistant-1")),))
    node, _think_ports, _act_ports, _act_node = _react(ports, policy=lambda _result: ReActRoute.ASSISTANT)

    result = await node.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    assert result.state.completion_route == ReActRoute.ASSISTANT.value
    assert ports.read_cursors == [cursor(0)]


@pytest.mark.asyncio
async def test_think_route_cycles_through_think_then_observe_with_the_next_cursor() -> None:
    ports = ObservePorts(
        (
            available(delivery(0, UserObservation(ObservationText("question")), "user-1")),
            available(delivery(1, ConfigObservation(ObservationText("done")), "config-1")),
        )
    )
    think_ports = ThinkPorts()
    route_calls = 0

    def policy(_result: ObserveResult) -> ReActRoute:
        nonlocal route_calls
        route_calls += 1
        return ReActRoute.ACT if route_calls == 1 else ReActRoute.CONFIG

    node, _think_ports, _act_ports, _act_node = _react(ports, think_ports=think_ports, policy=policy)
    result = await node.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    assert result.state.completion_route == ReActRoute.CONFIG.value
    assert route_calls == 2
    assert ports.read_cursors == [cursor(0), cursor(1)]
    assert len(think_ports.requests) == 1
    assert think_ports.requests[0].hook_state.cursor == cursor(1)


@pytest.mark.asyncio
async def test_act_route_enters_the_nested_authorization_interrupt_with_projected_state() -> None:
    ports = ObservePorts((available(delivery(0, UserObservation(ObservationText("tool")), "user-1")),))
    act_ports = ActPorts()
    node, _think_ports, _act_ports, act_node = _react(
        ports,
        act_ports=act_ports,
        policy=lambda _result: ReActRoute.THINK,
    )

    result = await node.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert len(result.interrupts) == 1
    assert str(result.interrupts[0].node_id) == "authorize"
    assert result.interrupts[0].scope[-1] == "run"
    action = act_node.resume_authorization(
        awaiting=result,
        interrupt_id=str(result.interrupts[0].interrupt_id),
        decision=Allow(),
    )
    assert action.scope == ("act", "run")
    assert str(action.node_id) == "authorize"
    assert len(act_ports.requests) == 1
    assert act_ports.requests[0].hook_state.cursor == cursor(1)
    assert ports.read_cursors == [cursor(0)]


@pytest.mark.asyncio
async def test_empty_observe_read_stays_at_the_observe_wait_boundary() -> None:
    ports = ObservePorts((empty(0),))
    route_calls = 0

    def policy(_result: ObserveResult) -> ReActRoute:
        nonlocal route_calls
        route_calls += 1
        return ReActRoute.CONFIG

    node, _think_ports, _act_ports, _act_node = _react(ports, policy=policy)
    result = await node.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.AwaitingResumeResult)
    assert len(result.interrupts) == 1
    assert str(result.interrupts[0].node_id) == "get_observation"
    assert ports.waits and ports.waits[0].after_cursor == cursor(0)
    assert route_calls == 0

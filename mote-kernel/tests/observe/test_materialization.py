"""Observe's direct Hook-result materialization boundary."""

from __future__ import annotations

import pytest
from tests.loop.support import (
    ObservationText,
    ObserveCommand,
    ObservePorts,
    SharedState,
    available,
    cursor,
    delivery,
    observe_admission,
)

from mote_kernel.config import ConfigActivation
from mote_kernel.hooks.contract import HookActivationRequest, HookResult
from mote_kernel.observe.contract import (
    ObserveHookEnvelope,
    ObserveRequest,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.node import GetObservationNode, WriteObservationNode
from mote_kernel.state.graph_state import GraphNodeId


@pytest.mark.asyncio
async def test_write_observation_consumes_a_direct_hook_result_activation() -> None:
    read = available(delivery(0, UserObservation(ObservationText("question")), "user-1"))
    ports = ObservePorts((read,))
    admission = observe_admission()
    request = ObserveRequest(cursor(0), SharedState(cursor=cursor(0)))

    get_request = await GetObservationNode[SharedState, ObserveCommand](ports, ports, admission)(
        ConfigActivation(request)
    )
    assert type(get_request) is HookActivationRequest
    hook_result: HookResult[ObserveHookEnvelope, ObserveCommand] = HookResult(
        get_request.value,
        (ObserveCommand(),),
        GraphNodeId("get_observation"),
    )

    write_activation = await WriteObservationNode[SharedState, ObserveCommand](ports, ports, admission)(
        ConfigActivation(hook_result)
    )

    assert type(write_activation) is ConfigActivation
    assert write_activation.activation_config is None
    assert type(write_activation.value) is HookActivationRequest
    assert write_activation.value.node_id == GraphNodeId("write_observation")
    assert type(write_activation.value.value.payload) is WriteObservationStageValue

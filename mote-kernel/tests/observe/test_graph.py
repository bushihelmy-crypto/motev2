"""Graph-level behavior tests for the Observe nested graph boundary."""

from __future__ import annotations

import asyncio
from typing import Never, cast

import pytest
from tests.observe.test_nodes import (
    ObserveTestCommand as _Command,
)
from tests.observe.test_nodes import (
    ObserveTestHookInvocation as _HookInvocation,
)
from tests.observe.test_nodes import (
    ObserveTestPorts as _Ports,
)
from tests.observe.test_nodes import (
    ObserveTestPriority as _Priority,
)
from tests.observe.test_nodes import (
    ObserveTestState as _State,
)
from tests.observe.test_nodes import (
    builder_hook_is_shared,
    builder_node_ids,
)
from tests.observe.test_nodes import (
    make_available as _available,
)
from tests.observe.test_nodes import (
    make_cursor as _cursor,
)
from tests.observe.test_nodes import (
    make_delivery as _delivery,
)
from tests.observe.test_nodes import (
    make_hook as _hook,
)
from tests.observe.test_nodes import (
    make_observe as _observe,
)
from tests.observe.test_nodes import (
    make_observe_with_hook as _observe_with_hook,
)
from tests.observe.test_nodes import (
    make_request as _request,
)

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import GraphInputRef
from mote_kernel.hooks.contract import (
    HookGraphValue,
    HookInvocationRequest,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.observe import ObserveNode
from mote_kernel.observe.contract import (
    AssistantBatch,
    AssistantObservation,
    BackgroundTaskSnapshot,
    ConfigBatch,
    ConfigObservation,
    ConfigSettlementReceipt,
    ContextAppendReceipt,
    GetObservationStageValue,
    ObservationKind,
    ObservationRead,
    ObserveContractError,
    ObserveFrame,
    ObserveHookEnvelope,
    ObserveRequest,
    ToolBatch,
    UserBatch,
    UserObservation,
    WriteObservationStageValue,
)
from mote_kernel.observe.identity import ObservationBoundary, ObservationCursor, ObserveHookStage
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId


def test_observe_builder_contains_exactly_two_business_nodes_and_one_shared_hook() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    observe = _observe(ports, invocation)

    assert len(builder_node_ids(observe)) == 3
    assert builder_node_ids(observe) == (
        "get_observation",
        "hook",
        "write_observation",
    )
    assert builder_hook_is_shared(observe)


@pytest.mark.asyncio
async def test_observe_terminal_output_is_the_second_shared_hook_activation() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, AssistantObservation("answer"), "answer")))
    observe = _observe(ports, invocation)

    result = await observe.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"])
    assert output.node_id == GraphNodeId("write_observation")
    assert output.value.stage is ObserveHookStage.AFTER_WRITE_OBSERVATION
    assert isinstance(output.value.payload, WriteObservationStageValue)
    assert output.value.payload.result.current_state is ObservationKind.ASSISTANT
    assert result.state.completion_route == "write_observation"
    assert tuple(request.request.node_id for request in invocation.requests) == (
        GraphNodeId("get_observation"),
        GraphNodeId("get_observation"),
        GraphNodeId("get_observation"),
        GraphNodeId("write_observation"),
        GraphNodeId("write_observation"),
        GraphNodeId("write_observation"),
    )


@pytest.mark.asyncio
async def test_shared_hook_can_rewrite_the_stage_payload_but_not_its_graph_route_identity() -> None:
    class RewriteInvocation(_HookInvocation):
        async def invoke(
            self,
            request: HookInvocationRequest[_Priority, ObserveHookEnvelope, _State],
            /,
        ) -> HookStageResult[ObserveHookEnvelope, _Command]:
            self.requests.append(request)
            value = request.request.value
            if value.stage is ObserveHookStage.AFTER_GET_OBSERVATION and request.config.ordinal == 1:
                replacement = _available(_delivery(0, ConfigObservation("rewritten"), "rewritten"))
                value = ObserveHookEnvelope(
                    ObserveHookStage.AFTER_GET_OBSERVATION,
                    GetObservationStageValue(
                        ObserveFrame(
                            replacement.batch,
                            replacement.boundary,
                            BackgroundTaskSnapshot(1, ()),
                        )
                    ),
                    value.hook_state,
                )
            return HookStageResult(value, (_Command(value.stage.value),))

    ports = _Ports()
    invocation = RewriteInvocation()
    ports.reads.append(_available(_delivery(0, UserObservation("original"), "original")))
    observe = _observe_with_hook(ports, invocation, _hook("observe.test", invocation))

    result = await observe.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    output = cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"])
    payload = cast(WriteObservationStageValue, output.value.payload)
    assert output.node_id == GraphNodeId("write_observation")
    assert payload.result.current_state is ObservationKind.CONFIG
    assert payload.result.delivery_ids == (_delivery(0, ConfigObservation("rewritten"), "rewritten").delivery_id,)
    assert ports.configs == [payload.result.delivery_ids]
    assert ports.contexts == []


@pytest.mark.asyncio
async def test_nested_parent_consumes_observe_only_at_its_terminal_route() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    observe = _observe(ports, invocation)
    parent: Graph[HookGraphValue] = Graph("observe.parent")
    request_type = cast(type[ObserveRequest[_State]], ObserveRequest)
    request_input = cast(GraphInputRef[HookGraphValue], Graph.graph_input("request", request_type))
    parent.add_node("observe", observe, inputs={"request": request_input})

    async def consume(values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        return Graph.values(result=values["result"])

    parent.add_node(
        "consume",
        consume,
        inputs={"result": Graph.node_output("observe", "result")},
        outputs={"result": HookResult},
    )
    parent.add_edge("observe", "write_observation", "consume")
    parent.add_edge("consume", Graph.END)
    parent.set_outputs({"result": Graph.node_output("consume", "result")})
    ports.reads.append(_available(_delivery(0, UserObservation("query"), "query")))

    result = await parent.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    assert type(result.outputs["result"]) is HookResult


@pytest.mark.asyncio
async def test_two_concurrent_observe_runs_keep_frames_and_hook_state_isolated() -> None:
    class IsolatedPorts(_Ports):
        async def read_after(self, cursor: ObservationCursor, /) -> ObservationRead:
            await asyncio.sleep(0)
            return await super().read_after(cursor)

        async def snapshot(self, boundary: ObservationBoundary, /) -> BackgroundTaskSnapshot:
            await asyncio.sleep(0)
            return await super().snapshot(boundary)

        async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
            await asyncio.sleep(0)
            return await super().append(batch)

    ports = IsolatedPorts()
    invocation = _HookInvocation()
    ports.reads.extend(
        (
            _available(_delivery(0, UserObservation("first"), "first")),
            _available(_delivery(0, AssistantObservation("second"), "second")),
        )
    )
    observe = _observe(ports, invocation)

    first, second = await asyncio.gather(
        observe.run(Graph.values(request=ObserveRequest(_cursor(0), _State("first"))), run_id="observe-first"),
        observe.run(Graph.values(request=ObserveRequest(_cursor(0), _State("second"))), run_id="observe-second"),
    )

    assert isinstance(first, Graph.CompletedResult)
    assert isinstance(second, Graph.CompletedResult)
    outputs = tuple(
        cast(HookResult[ObserveHookEnvelope, _Command], result.outputs["result"]) for result in (first, second)
    )
    assert {cast(_State, output.value.hook_state).marker for output in outputs} == {"first", "second"}
    assert {cast(WriteObservationStageValue, output.value.payload).result.current_state for output in outputs} == {
        ObservationKind.USER,
        ObservationKind.ASSISTANT,
    }


class _FailingPorts(_Ports):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure

    def _fail(self, operation: str) -> None:
        if self.failure == operation:
            raise RuntimeError(f"{operation} failure")

    async def read_after(self, cursor: ObservationCursor, /) -> ObservationRead:
        self._fail("read")
        return await super().read_after(cursor)

    async def snapshot(self, boundary: ObservationBoundary, /) -> BackgroundTaskSnapshot:
        self._fail("snapshot")
        return await super().snapshot(boundary)

    async def apply(self, batch: ConfigBatch, /) -> ConfigSettlementReceipt:
        self._fail("config")
        return await super().apply(batch)

    async def append(self, batch: ToolBatch | UserBatch | AssistantBatch, /) -> ContextAppendReceipt:
        self._fail("context")
        return await super().append(batch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "hook_calls"),
    [("read", 0), ("snapshot", 0), ("config", 3), ("context", 3)],
)
async def test_observe_port_failure_stops_before_later_graph_stages(failure: str, hook_calls: int) -> None:
    ports = _FailingPorts(failure)
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, ConfigObservation("config"), "config")))
    if failure == "context":
        ports.reads.clear()
        ports.reads.append(_available(_delivery(0, UserObservation("user"), "user")))
    observe = _observe(ports, invocation)

    with pytest.raises(RuntimeError, match=f"{failure} failure"):
        await observe.run(Graph.values(request=_request()))
    assert len(invocation.requests) == hook_calls


class _FailingHook(_HookInvocation):
    def __init__(self, node_id: str) -> None:
        super().__init__()
        self.node_id = node_id

    async def invoke(
        self,
        request: HookInvocationRequest[_Priority, ObserveHookEnvelope, _State],
        /,
    ) -> HookStageResult[ObserveHookEnvelope, _Command]:
        self.requests.append(request)
        if request.request.node_id == GraphNodeId(self.node_id):
            raise RuntimeError(f"{self.node_id} hook failure")
        return HookStageResult(request.request.value, ())


@pytest.mark.asyncio
@pytest.mark.parametrize(("node_id", "hook_calls"), [("get_observation", 1), ("write_observation", 4)])
async def test_observe_hook_failure_does_not_advance_to_a_later_stage(node_id: str, hook_calls: int) -> None:
    ports = _Ports()
    invocation = _FailingHook(node_id)
    ports.reads.append(_available(_delivery(0, UserObservation("user"), "user")))

    with pytest.raises(RuntimeError, match=f"{node_id} hook failure"):
        await _observe_with_hook(ports, invocation, _hook("observe.test", invocation)).run(
            Graph.values(request=_request())
        )
    assert len(invocation.requests) == hook_calls


@pytest.mark.asyncio
async def test_observe_rejects_a_wrong_graph_input_before_any_capability_call() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    observe = _observe(ports, invocation)

    with pytest.raises(Graph.ValueAdmissionError, match="exact declared type"):
        await observe.run(Graph.values(request=cast(HookGraphValue, object())))
    assert ports.reads == []
    assert invocation.requests == []


@pytest.mark.asyncio
async def test_observe_typed_materializer_rejects_a_missing_graph_input_before_any_capability_call() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    observe = _observe(ports, invocation)

    with pytest.raises(Graph.ValueAdmissionError, match="required input"):
        await observe.run(Graph.values())
    assert ports.reads == []
    assert invocation.requests == []


def test_observe_graph_becomes_immutable_after_first_compile() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    ports.reads.append(_available(_delivery(0, UserObservation("user"), "user")))
    observe = _observe(ports, invocation)

    asyncio.run(observe.run(Graph.values(request=_request())))

    async def late_node(_values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        return Graph.values()

    with pytest.raises(Graph.ValidationError, match="immutable"):
        observe.add_node("late", late_node, inputs={}, outputs={})
    with pytest.raises(Graph.ValidationError, match="immutable"):
        observe.add_edge("write_observation", Graph.END)
    with pytest.raises(Graph.ValidationError, match="immutable"):
        observe.set_outputs({"result": Graph.node_output("hook", "result")})


@pytest.mark.parametrize(
    ("definition_id", "version", "node_id"),
    [
        ("other.observe", 1, "hook"),
        ("observe.test", 2, "hook"),
        ("observe.test", 1, "other-hook"),
    ],
)
def test_observe_rejects_a_hook_with_an_incompatible_slot(definition_id: str, version: int, node_id: str) -> None:
    invocation = _HookInvocation()
    hook = _hook("observe.test", invocation)
    slot = HookSlotId(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(version),
        GraphNodeId(node_id),
        HookStage.AFTER_NODE,
    )
    object.__setattr__(hook, "_slot", slot)
    with pytest.raises(ObserveContractError, match="does not match"):
        _observe_with_hook(_Ports(), invocation, hook)


def test_observe_requires_a_real_shared_hook_and_concrete_admission() -> None:
    ports = _Ports()
    invocation = _HookInvocation()
    with pytest.raises(ObserveContractError, match="shared HookNode"):
        _observe_with_hook(ports, invocation, cast(Never, None))
    with pytest.raises(ObserveContractError, match="ObservePayloadAdmission"):
        ObserveNode(
            "observe.test",
            queue_port=ports,
            background_task_port=ports,
            config_port=ports,
            context_port=ports,
            ack_port=ports,
            resume_port=ports,
            hook=_hook("observe.test", invocation),
            admission=cast(Never, object()),
        )

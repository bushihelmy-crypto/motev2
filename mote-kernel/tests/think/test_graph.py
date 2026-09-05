from __future__ import annotations

from dataclasses import dataclass
from typing import Never, cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import GraphInputRef
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import (
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan, HookPriorityPlan
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId
from mote_kernel.think import ThinkNode
from mote_kernel.think.contract import (
    CommandStep,
    CompactedContext,
    CompactRequest,
    CompactStep,
    ContextFrame,
    ContextRequest,
    ContextStep,
    InferenceRequest,
    InferenceResult,
    InferenceStep,
    ModelBinding,
    PromptFrame,
    PromptStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkRoute,
    ThinkStep,
)
from mote_kernel.think.node import _RouteNode  # pyright: ignore[reportPrivateUsage]


@dataclass(frozen=True, slots=True)
class Payload(HookGraphValue):
    text: str


@dataclass(frozen=True, slots=True)
class State(HookGraphValue):
    turn: int


@dataclass(frozen=True, slots=True)
class Command(HookGraphValue):
    value: str


@dataclass(frozen=True, slots=True)
class Config:
    marker: str = "x"


@dataclass(frozen=True, slots=True)
class Priority:
    index: int


class ConfigSource:
    def snapshot(self) -> HookConfigSnapshot[Config]:
        return HookConfigSnapshot(Config())


class PlanLoader:
    def load(self, snapshot: HookConfigSnapshot[Config], /) -> HookPlan[Priority]:
        return HookPlan(HookPriorityPlan(Priority(1)), HookPriorityPlan(Priority(2)), HookPriorityPlan(Priority(3)))


class HookRuntime:
    def __init__(self) -> None:
        self.calls: list[HookInvocationRequest[Priority, ThinkFrame[ThinkStep, State], State]] = []

    async def invoke(
        self,
        request: HookInvocationRequest[Priority, ThinkFrame[ThinkStep, State], State],
        /,
    ) -> HookStageResult[ThinkFrame[ThinkStep, State], Command]:
        self.calls.append(request)
        return HookStageResult(request.request.value, (Command(str(type(request.request.value.step).__name__)),))


class Ports:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def load_system_prompt(self, payload: Payload, /) -> str:
        self.calls.append("system")
        return "system"

    async def load_placeholder(self, payload: Payload, /) -> str:
        self.calls.append("placeholder")
        return "placeholder"

    async def load_user_prompt(self, payload: Payload, /) -> str:
        self.calls.append("user")
        return payload.text

    async def load_context(
        self, request: ContextRequest[Payload, State, str, str, str], /
    ) -> ContextFrame[tuple[str, ...]]:
        self.calls.append("context")
        return ContextFrame((request.request.payload.text,))

    async def compact(
        self, request: CompactRequest[str, str, str, tuple[str, ...]], /
    ) -> CompactedContext[tuple[str, ...]]:
        self.calls.append("compact")
        return CompactedContext(request.context.snapshot, 1)

    async def infer(self, request: InferenceRequest[str, str, str, tuple[str, ...]], /) -> InferenceResult[str]:
        self.calls.append("inference")
        return InferenceResult(request.prompt.user)

    async def build_command(self, request: InferenceResult[str], /) -> ThinkCoreResult[str]:
        self.calls.append("command")
        return ThinkCoreResult(request.output)


class FailingContextPorts(Ports):
    async def load_context(
        self,
        request: ContextRequest[Payload, State, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        self.calls.append("context")
        raise RuntimeError("context unavailable")


def make_hook(
    runtime: HookRuntime,
    *,
    definition_id: str = "think.test",
    version: int = 1,
    node_id: str = "hook",
    stage: HookStage = HookStage.AFTER_NODE,
) -> HookNode[Config, Priority, ThinkFrame[ThinkStep, State], State, Command]:
    admission = HookPayloadAdmission[Config, Priority, ThinkFrame[ThinkStep, State], State, Command](
        Config,
        Priority,
        ThinkFrame,
        State,
        Command,
    )
    hook: HookNode[Config, Priority, ThinkFrame[ThinkStep, State], State, Command] = HookNode(
        HookSlotId(
            GraphDefinitionId(definition_id),
            GraphDefinitionVersion(version),
            GraphNodeId(node_id),
            stage,
        ),
        ConfigSource(),
        PlanLoader(),
        runtime,
        admission,
    )
    return hook


def make_think(runtime: HookRuntime, ports: Ports) -> ThinkNode[Config, Priority, State, Command]:
    hook = make_hook(runtime)
    return ThinkNode(
        "think.test",
        prompt_port=ports,
        context_port=ports,
        compact_port=ports,
        inference_port=ports,
        command_port=ports,
        model_binding=ModelBinding("provider", "model", 1),
        hook=hook,
    )


@pytest.mark.asyncio
async def test_full_graph() -> None:
    runtime = HookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)
    result = await think.run(Graph.values(request=ThinkRequest(Payload("hello"), State(1))))
    assert isinstance(result, Graph.CompletedResult)
    assert ports.calls == ["system", "placeholder", "user", "context", "compact", "inference", "command"]
    assert len(runtime.calls) == 15
    output = cast(HookResult[ThinkFrame[object, State], Command], result.outputs["result"])
    assert type(output.value) is ThinkFrame


@pytest.mark.asyncio
async def test_nested_parent_sees_only_think_boundary() -> None:
    runtime = HookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)

    parent: Graph[HookGraphValue] = Graph("react.test")
    request_type = cast(type[ThinkRequest[Payload, State]], ThinkRequest)
    parent_request = cast(GraphInputRef[HookGraphValue], Graph.graph_input("request", request_type))
    parent.add_node("think", think, inputs={"request": parent_request})

    async def consume(values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        raw_result = values["result"]
        assert type(raw_result) is HookResult
        result = cast(HookResult[ThinkFrame[ThinkStep, State], Command], raw_result)
        return Graph.values(result=result)

    parent.add_node(
        "consume",
        consume,
        inputs={"result": Graph.node_output("think", "result")},
        outputs={"result": HookResult},
    )
    parent.add_edge("think", "consume")
    parent.add_edge("consume", Graph.END)
    parent.set_outputs({"result": Graph.node_output("consume", "result")})

    result = await parent.run(Graph.values(request=ThinkRequest(Payload("nested"), State(2))))
    assert isinstance(result, Graph.CompletedResult)
    assert type(result.outputs["result"]) is HookResult


@pytest.mark.asyncio
async def test_port_exception_stops_the_graph_before_later_stages() -> None:
    runtime = HookRuntime()
    ports = FailingContextPorts()
    think = make_think(runtime, ports)

    with pytest.raises(RuntimeError, match="context unavailable"):
        await think.run(Graph.values(request=ThinkRequest(Payload("failed"), State(3))))
    assert ports.calls == ["system", "placeholder", "user", "context"]
    assert len(runtime.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("step", "route"),
    [
        (PromptStep(PromptFrame("system", "placeholder", "user")), ThinkRoute.CONTEXT.value),
        (
            ContextStep(
                PromptFrame("system", "placeholder", "user"),
                ContextFrame(("history",)),
            ),
            ThinkRoute.COMPACT.value,
        ),
        (
            CompactStep(
                PromptFrame("system", "placeholder", "user"),
                ContextFrame(("history",)),
                CompactedContext(("history",), 1),
            ),
            ThinkRoute.INFERENCE.value,
        ),
        (
            InferenceStep(
                PromptFrame("system", "placeholder", "user"),
                CompactedContext(("history",), 1),
                InferenceResult("answer"),
            ),
            ThinkRoute.COMMAND.value,
        ),
        (
            CommandStep(
                PromptFrame("system", "placeholder", "user"),
                CompactedContext(("history",), 1),
                InferenceResult("answer"),
                ThinkCoreResult("command"),
            ),
            ThinkRoute.FINISH.value,
        ),
    ],
)
async def test_route_maps_every_known_step_and_preserves_result(step: ThinkStep, route: str) -> None:
    hook_result = HookResult(ThinkFrame(step, State(1)), (Command("opaque"),))
    output = await _RouteNode()(Graph.values(result=hook_result))

    assert isinstance(output, Graph.SuccessOutcome)
    assert output.route == route
    assert output.output["hook_result"] is hook_result


@pytest.mark.asyncio
async def test_route_rejects_wrong_outer_and_inner_values() -> None:
    route = _RouteNode()
    with pytest.raises(ThinkContractError, match="HookResult"):
        await route(Graph.values(result=cast(HookGraphValue, object())))
    with pytest.raises(ThinkContractError, match="ThinkFrame"):
        await route(Graph.values(result=HookResult(cast(Never, object()), ())))


@pytest.mark.asyncio
async def test_route_rejects_an_unknown_step_even_inside_a_malformed_frame() -> None:
    malformed = cast(ThinkFrame[ThinkStep, State], object.__new__(ThinkFrame))
    object.__setattr__(malformed, "step", ThinkStep())
    object.__setattr__(malformed, "hook_state", State(1))

    with pytest.raises(ThinkContractError, match="unknown ThinkStep"):
        malformed_result: HookResult[ThinkFrame[ThinkStep, State], Command] = HookResult(malformed, ())
        await _RouteNode()(Graph.values(result=malformed_result))


def _think_with_hook(hook: object, *, definition_id: str = "think.test", version: int = 1) -> object:
    ports = Ports()
    return cast(
        object,
        ThinkNode[Config, Priority, State, Command](
            definition_id,
            version=version,
            prompt_port=ports,
            context_port=ports,
            compact_port=ports,
            inference_port=ports,
            command_port=ports,
            model_binding=ModelBinding("provider", "model", 1),
            hook=cast(Never, hook),
        ),
    )


def _malformed_hook_with_slot(slot: object) -> HookNode[Config, Priority, ThinkFrame[ThinkStep, State], State, Command]:
    hook = cast(HookNode[Config, Priority, ThinkFrame[ThinkStep, State], State, Command], object.__new__(HookNode))
    object.__setattr__(hook, "_slot", slot)
    return hook


def test_think_rejects_non_hook_children_before_graph_assembly() -> None:
    with pytest.raises(ThinkContractError, match="shared HookNode"):
        _think_with_hook(None)
    fake_graph: Graph[HookGraphValue] = Graph("not-a-hook")
    with pytest.raises(ThinkContractError, match="shared HookNode"):
        _think_with_hook(fake_graph)

    async def not_a_hook(_values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        return cast(Graph.Values[HookGraphValue], Graph.values())

    with pytest.raises(ThinkContractError, match="shared HookNode"):
        _think_with_hook(not_a_hook)


def test_think_rejects_a_hook_with_a_non_slot_property() -> None:
    malformed = _malformed_hook_with_slot(cast(Never, object()))
    with pytest.raises(ThinkContractError, match="HookSlotId"):
        _think_with_hook(malformed)


@pytest.mark.parametrize(
    ("definition_id", "version", "node_id"),
    [
        ("other.think", 1, "hook"),
        ("think.test", 2, "hook"),
        ("think.test", 1, "other-hook"),
    ],
)
def test_think_rejects_a_hook_with_an_incompatible_slot(
    definition_id: str,
    version: int,
    node_id: str,
) -> None:
    hook = make_hook(
        HookRuntime(),
        definition_id=definition_id,
        version=version,
        node_id=node_id,
    )
    with pytest.raises(ThinkContractError, match="does not match"):
        _think_with_hook(hook)


def test_think_exposes_the_same_shared_hook() -> None:
    runtime = HookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)
    assert think.hook is think.hook

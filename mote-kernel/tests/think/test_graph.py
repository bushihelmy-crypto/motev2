from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Generic, Never, TypeVar, cast

import pytest

from mote_kernel.execution import Graph
from mote_kernel.execution.graph.ports import GraphInputRef
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import (
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookRequest,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan, HookPriorityPlan
from mote_kernel.invocation import InvocationTypeContract
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
    RouterRequest,
    RouterStep,
    ThinkContractError,
    ThinkCoreResult,
    ThinkFrame,
    ThinkRequest,
    ThinkStep,
)
from mote_kernel.think.port import CommandPort, CompactPort, ContextPort, InferencePort, PromptPort, RouterPort

InvocationRequestT = TypeVar("InvocationRequestT")
InvocationResultT = TypeVar("InvocationResultT")


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

    async def route_model(
        self,
        request: RouterRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> ModelBinding:
        self.calls.append("router")
        return ModelBinding("provider", "model", 1)

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


class StageFailurePorts(Ports):
    """A deterministic Port double that fails at exactly one operation."""

    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure

    def _maybe_fail(self, operation: str) -> None:
        if self.failure == operation:
            raise RuntimeError(f"{operation} failure")

    async def load_system_prompt(self, payload: Payload, /) -> str:
        self.calls.append("system")
        self._maybe_fail("system")
        return "system"

    async def load_placeholder(self, payload: Payload, /) -> str:
        self.calls.append("placeholder")
        self._maybe_fail("placeholder")
        return "placeholder"

    async def load_user_prompt(self, payload: Payload, /) -> str:
        self.calls.append("user")
        self._maybe_fail("user")
        return payload.text

    async def load_context(
        self,
        request: ContextRequest[Payload, State, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        self.calls.append("context")
        self._maybe_fail("context")
        return ContextFrame((request.request.payload.text,))

    async def compact(
        self,
        request: CompactRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> CompactedContext[tuple[str, ...]]:
        self.calls.append("compact")
        self._maybe_fail("compact")
        return CompactedContext(request.context.snapshot, 1)

    async def route_model(
        self,
        request: RouterRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> ModelBinding:
        self.calls.append("router")
        self._maybe_fail("router")
        return ModelBinding("provider", "model", 1)

    async def infer(self, request: InferenceRequest[str, str, str, tuple[str, ...]], /) -> InferenceResult[str]:
        self.calls.append("inference")
        self._maybe_fail("inference")
        return InferenceResult(request.prompt.user)

    async def build_command(self, request: InferenceResult[str], /) -> ThinkCoreResult[str]:
        self.calls.append("command")
        self._maybe_fail("command")
        return ThinkCoreResult(request.output)


class IsolatedPorts(Ports):
    """A Port double whose outputs are derived only from the current payload."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, str]] = []

    async def load_system_prompt(self, payload: Payload, /) -> str:
        await asyncio.sleep(0)
        self.events.append(("system", payload.text))
        return f"system:{payload.text}"

    async def load_placeholder(self, payload: Payload, /) -> str:
        await asyncio.sleep(0)
        self.events.append(("placeholder", payload.text))
        return f"placeholder:{payload.text}"

    async def load_user_prompt(self, payload: Payload, /) -> str:
        await asyncio.sleep(0)
        self.events.append(("user", payload.text))
        return f"user:{payload.text}"

    async def load_context(
        self,
        request: ContextRequest[Payload, State, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        await asyncio.sleep(0)
        payload = request.request.payload.text
        self.events.append(("context", payload))
        return ContextFrame((f"history:{payload}",))

    async def compact(
        self,
        request: CompactRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> CompactedContext[tuple[str, ...]]:
        await asyncio.sleep(0)
        payload = request.context.snapshot[0].split(":", 1)[1]
        self.events.append(("compact", payload))
        return CompactedContext(request.context.snapshot, 1)

    async def route_model(
        self,
        request: RouterRequest[str, str, str, tuple[str, ...]],
        /,
    ) -> ModelBinding:
        await asyncio.sleep(0)
        payload = request.prompt.user.split(":", 1)[1]
        self.events.append(("router", payload))
        return ModelBinding("provider", f"model-{payload}", 1)

    async def infer(self, request: InferenceRequest[str, str, str, tuple[str, ...]], /) -> InferenceResult[str]:
        await asyncio.sleep(0)
        payload = request.prompt.user.split(":", 1)[1]
        self.events.append(("inference", payload))
        return InferenceResult(f"answer:{payload}")

    async def build_command(self, request: InferenceResult[str], /) -> ThinkCoreResult[str]:
        await asyncio.sleep(0)
        payload = request.output.split(":", 1)[1]
        self.events.append(("command", payload))
        return ThinkCoreResult(f"command:{payload}")


class BlockingContextPorts(Ports):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def load_context(
        self,
        request: ContextRequest[Payload, State, str, str, str],
        /,
    ) -> ContextFrame[tuple[str, ...]]:
        self.calls.append("context")
        self.entered.set()
        await self.release.wait()
        return ContextFrame((request.request.payload.text,))


class FailingHookRuntime(HookRuntime):
    def __init__(self, failure_node: str) -> None:
        super().__init__()
        self.failure_node = failure_node

    async def invoke(
        self,
        request: HookInvocationRequest[Priority, ThinkFrame[ThinkStep, State], State],
        /,
    ) -> HookStageResult[ThinkFrame[ThinkStep, State], Command]:
        self.calls.append(request)
        if request.request.node_id == GraphNodeId(self.failure_node):
            raise RuntimeError(f"{self.failure_node} hook failure")
        return HookStageResult(request.request.value, (Command(str(type(request.request.value.step).__name__)),))


class BlockingHookRuntime(HookRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(
        self,
        request: HookInvocationRequest[Priority, ThinkFrame[ThinkStep, State], State],
        /,
    ) -> HookStageResult[ThinkFrame[ThinkStep, State], Command]:
        self.calls.append(request)
        self.entered.set()
        await self.release.wait()
        return HookStageResult(request.request.value, (Command(str(type(request.request.value.step).__name__)),))


@dataclass(frozen=True, slots=True)
class RecordingInvocation(Generic[InvocationRequestT, InvocationResultT]):
    result: InvocationResultT
    calls: list[InvocationRequestT]

    async def invoke(self, request: InvocationRequestT, /) -> InvocationResultT:
        self.calls.append(request)
        return self.result


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
        router_port=ports,
        inference_port=ports,
        command_port=ports,
        hook_state_type=State,
        hook=hook,
    )


@pytest.mark.asyncio
async def test_full_graph() -> None:
    runtime = HookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)
    request = ThinkRequest(Payload("hello"), State(1))
    result = await think.run(Graph.values(request=request))
    assert isinstance(result, Graph.CompletedResult)
    assert ports.calls == ["system", "placeholder", "user", "context", "compact", "router", "inference", "command"]
    assert len(runtime.calls) == 18
    assert [str(call.request.node_id) for call in runtime.calls] == [
        *(["prompt"] * 3),
        *(["context"] * 3),
        *(["compact"] * 3),
        *(["router"] * 3),
        *(["inference"] * 3),
        *(["command"] * 3),
    ]
    output = cast(HookResult[ThinkFrame[object, State], Command], result.outputs["result"])
    assert type(output.value) is ThinkFrame
    assert output.node_id == GraphNodeId("command")
    assert output.value.hook_state is request.hook_state
    raw_step: object = output.value.step
    assert type(raw_step) is CommandStep
    final_step = cast(CommandStep[str, str, str, tuple[str, ...], str, str], raw_step)
    assert final_step.core.command == "hello"
    # Hook commands belong to the terminal Hook activation's result.  Think
    # forwards that result unchanged; it does not merge commands from earlier
    # Hook activations into a second command stream.
    assert output.commands == (Command("CommandStep"),) * 3
    assert result.state.completion_route == "command"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_id", "step"),
    [
        ("prompt", PromptStep(PromptFrame("system", "placeholder", "user"))),
        ("context", ContextStep(PromptFrame("system", "placeholder", "user"), ContextFrame(("history",)))),
        (
            "compact",
            CompactStep(
                PromptFrame("system", "placeholder", "user"),
                ContextFrame(("history",)),
                CompactedContext(("history",), 1),
            ),
        ),
        (
            "router",
            RouterStep(
                PromptFrame("system", "placeholder", "user"),
                CompactedContext(("history",), 1),
                ModelBinding("provider", "model", 1),
            ),
        ),
        (
            "inference",
            InferenceStep(
                PromptFrame("system", "placeholder", "user"),
                CompactedContext(("history",), 1),
                ModelBinding("provider", "model", 1),
                InferenceResult("answer"),
            ),
        ),
        (
            "command",
            CommandStep(
                PromptFrame("system", "placeholder", "user"),
                CompactedContext(("history",), 1),
                ModelBinding("provider", "model", 1),
                InferenceResult("answer"),
                ThinkCoreResult("command"),
            ),
        ),
    ],
)
async def test_shared_hook_exports_the_originating_node_route(node_id: str, step: ThinkStep) -> None:
    """The new API carries the route on the shared Hook terminal result."""

    runtime = HookRuntime()
    hook = make_hook(runtime)
    state = State(1)
    frame = ThinkFrame(step, state)
    request = HookRequest(frame, state, GraphNodeId(node_id))

    result = await hook.run(Graph.values(request=request))

    assert isinstance(result, Graph.CompletedResult)
    hook_result = cast(HookResult[ThinkFrame[ThinkStep, State], Command], result.outputs["result"])
    assert hook_result.value is frame
    assert hook_result.node_id == GraphNodeId(node_id)
    assert result.state.completion_route == node_id


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
    # Think exports the terminal Hook route through the nested boundary; the
    # parent must consume that route with a conditional edge in the new Graph
    # API rather than treating the child as an ordinary direct successor.
    parent.add_edge("think", "command", "consume")
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


def _think_with_hook(
    hook: object,
    *,
    definition_id: str = "think.test",
    version: int = 1,
    hook_state_type: type[State] = State,
) -> object:
    ports = Ports()
    return cast(
        object,
        ThinkNode[Config, Priority, State, Command](
            definition_id,
            version=version,
            prompt_port=ports,
            context_port=ports,
            compact_port=ports,
            router_port=ports,
            inference_port=ports,
            command_port=ports,
            hook_state_type=hook_state_type,
            hook=cast(Never, hook),
        ),
    )


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


def test_think_rejects_a_shared_hook_with_a_mismatched_state_type() -> None:
    class OtherState(HookGraphValue):
        __slots__ = ()

    with pytest.raises(ThinkContractError, match="state type"):
        _think_with_hook(make_hook(HookRuntime()), hook_state_type=OtherState)  # type: ignore[arg-type]


@pytest.mark.parametrize("state_type", (HookGraphValue, str))
def test_think_requires_a_concrete_hook_graph_value_state_type(state_type: type[object]) -> None:
    with pytest.raises(ThinkContractError, match="concrete HookGraphValue"):
        _think_with_hook(
            make_hook(HookRuntime()),
            hook_state_type=cast(type[State], state_type),
        )


@pytest.mark.asyncio
async def test_think_graph_keeps_two_concurrent_runs_isolated() -> None:
    runtime = HookRuntime()
    ports = IsolatedPorts()
    think = make_think(runtime, ports)
    first_request = ThinkRequest(Payload("first"), State(11))
    second_request = ThinkRequest(Payload("second"), State(22))

    first, second = await asyncio.gather(
        think.run(Graph.values(request=first_request), run_id="think-first"),
        think.run(Graph.values(request=second_request), run_id="think-second"),
    )

    assert isinstance(first, Graph.CompletedResult)
    assert isinstance(second, Graph.CompletedResult)
    assert first.state is not second.state
    outputs: dict[int, HookResult[ThinkFrame[ThinkStep, State], Command]] = {}
    for result in (first, second):
        output = result.outputs["result"]
        assert type(output) is HookResult
        typed_output = cast(HookResult[ThinkFrame[ThinkStep, State], Command], output)
        outputs[typed_output.value.hook_state.turn] = typed_output

    assert set(outputs) == {11, 22}
    for turn, payload in ((11, "first"), (22, "second")):
        output = outputs[turn]
        assert output.node_id == GraphNodeId("command")
        raw_step = output.value.step
        assert type(raw_step) is CommandStep
        step = cast(CommandStep[str, str, str, tuple[str, ...], str, str], raw_step)
        assert step.core.command == f"command:{payload}"
        assert output.value.hook_state is (first_request.hook_state if turn == 11 else second_request.hook_state)

    for payload in ("first", "second"):
        assert [operation for operation, seen in ports.events if seen == payload] == [
            "system",
            "placeholder",
            "user",
            "context",
            "compact",
            "router",
            "inference",
            "command",
        ]
    assert len(runtime.calls) == 36
    assert {call.request.state.turn for call in runtime.calls} == {11, 22}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_calls", "expected_hook_calls"),
    [
        ("system", ["system"], 0),
        ("placeholder", ["system", "placeholder"], 0),
        ("user", ["system", "placeholder", "user"], 0),
        ("context", ["system", "placeholder", "user", "context"], 3),
        ("compact", ["system", "placeholder", "user", "context", "compact"], 6),
        ("router", ["system", "placeholder", "user", "context", "compact", "router"], 9),
        (
            "inference",
            ["system", "placeholder", "user", "context", "compact", "router", "inference"],
            12,
        ),
        (
            "command",
            ["system", "placeholder", "user", "context", "compact", "router", "inference", "command"],
            15,
        ),
    ],
)
async def test_think_port_failure_stops_at_the_failed_stage(
    failure: str,
    expected_calls: list[str],
    expected_hook_calls: int,
) -> None:
    runtime = HookRuntime()
    ports = StageFailurePorts(failure)
    think = make_think(runtime, ports)

    with pytest.raises(RuntimeError, match=f"{failure} failure"):
        await think.run(Graph.values(request=ThinkRequest(Payload("failure"), State(1))))

    assert ports.calls == expected_calls
    assert len(runtime.calls) == expected_hook_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_node", "expected_hook_calls"),
    [("prompt", 1), ("context", 4), ("compact", 7), ("router", 10), ("inference", 13), ("command", 16)],
)
async def test_think_hook_failure_stops_without_advancing_to_a_later_stage(
    failure_node: str,
    expected_hook_calls: int,
) -> None:
    runtime = FailingHookRuntime(failure_node)
    ports = Ports()
    think = make_think(runtime, ports)

    with pytest.raises(RuntimeError, match=f"{failure_node} hook failure"):
        await think.run(Graph.values(request=ThinkRequest(Payload("hook-failure"), State(2))))

    assert len(runtime.calls) == expected_hook_calls
    expected_port_calls = {
        "prompt": ["system", "placeholder", "user"],
        "context": ["system", "placeholder", "user", "context"],
        "compact": ["system", "placeholder", "user", "context", "compact"],
        "router": ["system", "placeholder", "user", "context", "compact", "router"],
        "inference": ["system", "placeholder", "user", "context", "compact", "router", "inference"],
        "command": ["system", "placeholder", "user", "context", "compact", "router", "inference", "command"],
    }
    assert ports.calls == expected_port_calls[failure_node]


@pytest.mark.asyncio
async def test_think_propagates_caller_cancellation_while_a_port_is_waiting() -> None:
    runtime = HookRuntime()
    ports = BlockingContextPorts()
    think = make_think(runtime, ports)
    task = asyncio.create_task(think.run(Graph.values(request=ThinkRequest(Payload("cancel"), State(3)))))

    await asyncio.wait_for(ports.entered.wait(), timeout=1)
    task.cancel("caller cancelled Think")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ports.calls == ["system", "placeholder", "user", "context"]
    assert len(runtime.calls) == 3


@pytest.mark.asyncio
async def test_think_propagates_caller_cancellation_while_shared_hook_is_waiting() -> None:
    runtime = BlockingHookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)
    task = asyncio.create_task(think.run(Graph.values(request=ThinkRequest(Payload("cancel-hook"), State(4)))))

    await asyncio.wait_for(runtime.entered.wait(), timeout=1)
    task.cancel("caller cancelled shared Hook")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ports.calls == ["system", "placeholder", "user"]
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_nested_think_propagates_root_cancellation() -> None:
    runtime = HookRuntime()
    ports = BlockingContextPorts()
    think = make_think(runtime, ports)
    parent: Graph[HookGraphValue] = Graph("think.cancel.parent")
    request_type = cast(type[HookGraphValue], ThinkRequest)
    parent.add_node(
        "think",
        think,
        inputs={"request": Graph.graph_input("request", request_type)},
    )
    parent.set_outputs({"result": Graph.node_output("think", "result")})

    task = asyncio.create_task(
        parent.run(
            Graph.values(request=ThinkRequest(Payload("nested-cancel"), State(5))),
            run_id="nested-cancel-run",
        )
    )
    await asyncio.wait_for(ports.entered.wait(), timeout=1)
    task.cancel("caller cancelled nested Think")
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ports.calls == ["system", "placeholder", "user", "context"]
    assert len(runtime.calls) == 3


@pytest.mark.asyncio
async def test_nested_think_forwards_terminal_result_and_all_commands_to_parent() -> None:
    runtime = HookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)
    parent: Graph[HookGraphValue] = Graph("think.result.parent")
    request_type = cast(type[HookGraphValue], ThinkRequest)
    parent.add_node(
        "think",
        think,
        inputs={"request": Graph.graph_input("request", request_type)},
    )

    async def consume(values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        return Graph.values(result=values["result"])

    parent.add_node(
        "consume",
        consume,
        inputs={"result": Graph.node_output("think", "result")},
        outputs={"result": HookResult},
    )
    parent.add_edge("think", "command", "consume")
    parent.add_edge("consume", Graph.END)
    parent.set_outputs({"result": Graph.node_output("consume", "result")})

    result = await parent.run(Graph.values(request=ThinkRequest(Payload("parent"), State(6))))

    assert isinstance(result, Graph.CompletedResult)
    output = result.outputs["result"]
    assert type(output) is HookResult
    typed_output = cast(HookResult[ThinkFrame[ThinkStep, State], Command], output)
    assert typed_output.node_id == GraphNodeId("command")
    assert typed_output.commands == (Command("CommandStep"),) * 3
    assert type(typed_output.value.step) is CommandStep


def test_think_graph_is_immutable_after_first_compile_for_every_builder_mutator() -> None:
    runtime = HookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)

    async def late_node(_values: Graph.Values[HookGraphValue], /) -> Graph.Values[HookGraphValue]:
        return Graph.values()

    # Compilation is synchronous inside the first run before the first await.
    # Use a completed coroutine to make the test independent of stage timing.
    asyncio.run(think.run(Graph.values(request=ThinkRequest(Payload("compile"), State(7)))))

    with pytest.raises(Graph.ValidationError, match="immutable"):
        think.add_node("late", late_node, inputs={}, outputs={})
    with pytest.raises(Graph.ValidationError, match="immutable"):
        think.add_edge("command", Graph.END)
    with pytest.raises(Graph.ValidationError, match="immutable"):
        think.set_outputs({"result": Graph.node_output("hook", "result")})


@pytest.mark.asyncio
async def test_think_rejects_a_wrong_graph_input_before_running_any_port() -> None:
    runtime = HookRuntime()
    ports = Ports()
    think = make_think(runtime, ports)

    with pytest.raises(Graph.ValueAdmissionError, match="exact declared type"):
        await think.run(Graph.values(request=cast(HookGraphValue, object())))
    assert ports.calls == []
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_think_invocation_backed_ports_preserve_typed_requests_end_to_end() -> None:
    payload = Payload("typed")
    state = State(8)
    request = ThinkRequest(payload, state)
    prompt_frame: PromptFrame[str, str, str] = PromptFrame("system-typed", "placeholder-typed", "user-typed")
    context_frame: ContextFrame[tuple[str, ...]] = ContextFrame(("history-typed",))
    compacted: CompactedContext[tuple[str, ...]] = CompactedContext(("compacted-typed",), 2)
    model = ModelBinding("provider", "typed-model", 1)
    inference_result: InferenceResult[str] = InferenceResult("answer-typed")
    core_result: ThinkCoreResult[str] = ThinkCoreResult("command-typed")

    system = RecordingInvocation[Payload, str]("system-typed", [])
    placeholder = RecordingInvocation[Payload, str]("placeholder-typed", [])
    user = RecordingInvocation[Payload, str]("user-typed", [])
    context = RecordingInvocation[ContextRequest[Payload, State, str, str, str], ContextFrame[tuple[str, ...]]](
        context_frame,
        [],
    )
    compact = RecordingInvocation[CompactRequest[str, str, str, tuple[str, ...]], CompactedContext[tuple[str, ...]]](
        compacted,
        [],
    )
    router = RecordingInvocation[
        RouterRequest[str, str, str, tuple[str, ...]],
        ModelBinding,
    ](model, [])
    inference = RecordingInvocation[
        InferenceRequest[str, str, str, tuple[str, ...]],
        InferenceResult[str],
    ](inference_result, [])
    command = RecordingInvocation[InferenceResult[str], ThinkCoreResult[str]](core_result, [])

    prompt_port: PromptPort[Payload, str, str, str] = PromptPort(
        system,
        placeholder,
        user,
        InvocationTypeContract(Payload, str),
        InvocationTypeContract(Payload, str),
        InvocationTypeContract(Payload, str),
    )
    context_port: ContextPort[
        ContextRequest[Payload, State, str, str, str],
        ContextFrame[tuple[str, ...]],
    ] = ContextPort(
        context,
        InvocationTypeContract[ContextRequest[Payload, State, str, str, str], ContextFrame[tuple[str, ...]]](
            ContextRequest,
            ContextFrame,
        ),
    )
    compact_port: CompactPort[
        CompactRequest[str, str, str, tuple[str, ...]],
        CompactedContext[tuple[str, ...]],
    ] = CompactPort(
        compact,
        InvocationTypeContract[CompactRequest[str, str, str, tuple[str, ...]], CompactedContext[tuple[str, ...]]](
            CompactRequest,
            CompactedContext,
        ),
    )
    router_port: RouterPort[RouterRequest[str, str, str, tuple[str, ...]]] = RouterPort(
        router,
        InvocationTypeContract[RouterRequest[str, str, str, tuple[str, ...]], ModelBinding](
            RouterRequest,
            ModelBinding,
        ),
    )
    inference_port: InferencePort[
        InferenceRequest[str, str, str, tuple[str, ...]],
        InferenceResult[str],
    ] = InferencePort(
        inference,
        InvocationTypeContract[InferenceRequest[str, str, str, tuple[str, ...]], InferenceResult[str]](
            InferenceRequest,
            InferenceResult,
        ),
    )
    command_port: CommandPort[InferenceResult[str], ThinkCoreResult[str]] = CommandPort(
        command,
        InvocationTypeContract[InferenceResult[str], ThinkCoreResult[str]](InferenceResult, ThinkCoreResult),
    )
    runtime = HookRuntime()
    think = ThinkNode(
        "think.typed",
        prompt_port=prompt_port,
        context_port=context_port,
        compact_port=compact_port,
        router_port=router_port,
        inference_port=inference_port,
        command_port=command_port,
        hook_state_type=State,
        hook=make_hook(runtime, definition_id="think.typed"),
    )

    result = await think.run(Graph.values(request=request))

    assert isinstance(result, Graph.CompletedResult)
    assert system.calls == [payload]
    assert placeholder.calls == [payload]
    assert user.calls == [payload]
    assert context.calls == [ContextRequest(request, prompt_frame)]
    assert compact.calls == [CompactRequest(prompt_frame, context_frame)]
    assert router.calls == [RouterRequest(prompt_frame, compacted)]
    assert inference.calls == [InferenceRequest(prompt_frame, compacted, model)]
    assert command.calls == [inference_result]
    output = cast(HookResult[ThinkFrame[ThinkStep, State], Command], result.outputs["result"])
    raw_step = output.value.step
    assert type(raw_step) is CommandStep
    assert cast(CommandStep[str, str, str, tuple[str, ...], str, str], raw_step).core == core_result

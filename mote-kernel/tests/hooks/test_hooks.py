"""Deterministic tests for the minimal graph-facing HookNode."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from typing import Protocol, cast

import pytest

import mote_kernel.hooks as hooks_package
import mote_kernel.hooks.contract as hooks_contract
import mote_kernel.invocation as invocation_package
from mote_kernel.config import Config, ConfigSnapshotKey
from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.config import HookPriorityConfig
from mote_kernel.hooks.contract import (
    HookActivationRequest,
    HookContractError,
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookResult,
    HookStageResult,
    HookTransitionAdmission,
)
from mote_kernel.hooks.identity import HookPriority, HookSlotId, HookStage, hook_definition_id
from mote_kernel.hooks.plan import HookPlan, HookPriorityPlan
from mote_kernel.hooks.port import HookPort
from mote_kernel.invocation import (
    Invocation,
    InvocationBoundaryAdmissionError,
    InvocationBoundaryError,
    InvocationTypeError,
)
from mote_kernel.state.graph_state import GraphConfigCursor, GraphDefinitionId, GraphDefinitionVersion, GraphNodeId


class _HookBuilderState(Protocol):
    nodes: tuple[object, ...]


class _InspectableHookGraph(Protocol):
    _builder_state: _HookBuilderState


class _HookBuilderNode(Protocol):
    invoker: object


class _HookInvoker(Protocol):
    operation: object


class _PriorityNodeView(Protocol):
    def _runtime(
        self,
        config: Config | None,
        /,
    ) -> tuple[
        HookPriorityPlan[PriorityConfig],
        HookPort[PriorityConfig, str, Counter, Increment],
    ]: ...


@dataclass(frozen=True, slots=True)
class PriorityConfig:
    rank: int
    fragments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Counter:
    value: int


@dataclass(frozen=True, slots=True)
class Increment:
    amount: int


@dataclass(frozen=True, slots=True)
class InvocationCall:
    config: PriorityConfig
    request: str


@dataclass(frozen=True, slots=True)
class TransitionCall:
    request: HookActivationRequest[str, Counter]
    result: HookStageResult[str, Increment]


class RecordingTransitionAdmission:
    def __init__(self, reject_rank: int | None = None) -> None:
        self.reject_rank = reject_rank
        self.calls: list[TransitionCall] = []
        self.failure = RuntimeError("transition rejected")

    def admit_transition(
        self,
        request: HookActivationRequest[str, Counter],
        result: HookStageResult[str, Increment],
        /,
    ) -> None:
        self.calls.append(TransitionCall(request, result))
        if len(self.calls) == self.reject_rank:
            raise self.failure


class NonCallableTransitionAdmission:
    admit_transition = None


class SerialRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        value = request
        for fragment in config.fragments:
            value = f"{value}{fragment}"
        commands = (Increment(config.rank),) if config.fragments else ()
        return HookStageResult(value, commands)


class ParallelRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    @staticmethod
    async def _fragment(fragment: str) -> str:
        await asyncio.sleep(0)
        return fragment

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        fragments = await asyncio.gather(*(self._fragment(fragment) for fragment in config.fragments))
        commands = (Increment(config.rank),) if fragments else ()
        return HookStageResult(f"{request}{''.join(fragments)}", commands)


class FailingRuntime:
    def __init__(self, failure_rank: int) -> None:
        self.failure_rank = failure_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.failure_rank:
            raise RuntimeError("invocation failed")
        return HookStageResult(f"{request}{''.join(config.fragments)}")


class CancellingRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        raise asyncio.CancelledError("invocation cancelled")


class RaisingTypeErrorRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []
        self.cause = InvocationTypeError("inner type failure")
        self.error = InvocationTypeError("runtime type failure")

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        self.calls.append(InvocationCall(invocation_request.hook_config, invocation_request.payload))
        raise self.error from self.cause


class RaisingBoundaryErrorRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []
        self.error = InvocationBoundaryError("runtime boundary failure")

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        self.calls.append(InvocationCall(invocation_request.hook_config, invocation_request.payload))
        raise self.error


class InvalidResultRuntime:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.invalid_rank:
            return cast(HookStageResult[str, Increment], object())
        return HookStageResult(f"{request}{''.join(config.fragments)}")


class FinalResultRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        return cast(HookStageResult[str, Increment], HookResult(request))


class DuplicateCommandRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        command = Increment(config.rank)
        return HookStageResult(request, (command, command))


class InvalidStageValueRuntime:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.invalid_rank:
            return HookStageResult(cast(str, object()))
        return HookStageResult(request)


class InvalidStageCommandRuntime:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.hook_config
        request = invocation_request.payload
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.invalid_rank:
            return HookStageResult(request, (cast(Increment, object()),))
        return HookStageResult(request)


class NonCallableInvocation:
    invoke = None


class PlanSubclass(HookPlan[PriorityConfig]):
    pass


class PriorityPlanSubclass(HookPriorityPlan[PriorityConfig]):
    pass


class RequestSubclass(HookActivationRequest[str, Counter]):
    pass


class InvocationRequestSubclass(HookInvocationRequest[PriorityConfig, str]):
    pass


class StageResultSubclass(HookStageResult[str, Increment]):
    pass


class ResultSubclass(HookResult[str, Increment]):
    pass


class SyncRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        self.calls.append(InvocationCall(invocation_request.hook_config, invocation_request.payload))
        return HookStageResult(invocation_request.payload)


class YieldingRuntime(SerialRuntime):
    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str],
        /,
    ) -> HookStageResult[str, Increment]:
        await asyncio.sleep(0)
        return await super().invoke(invocation_request)


def _slot(node_id: str = "observe") -> HookSlotId:
    return HookSlotId(
        GraphDefinitionId("react"),
        GraphDefinitionVersion(1),
        GraphNodeId(node_id),
    )


def _plan(prefix: str = "") -> HookPlan[PriorityConfig]:
    return HookPlan(
        HookPriorityPlan(PriorityConfig(1, (f"{prefix}1", f"{prefix}a"))),
        HookPriorityPlan(PriorityConfig(2, (f"{prefix}2", f"{prefix}b"))),
    )


def _node(
    invocation: Invocation[
        HookInvocationRequest[PriorityConfig, str],
        HookStageResult[str, Increment],
    ],
    plan: HookPlan[PriorityConfig] | None = None,
) -> HookNode[PriorityConfig, str, Counter, Increment]:
    return HookNode(_slot(), _plan() if plan is None else plan, invocation, _admission())


def _admission(
    transition_admission: HookTransitionAdmission[str, Counter, Increment] | None = None,
) -> HookPayloadAdmission[PriorityConfig, str, Counter, Increment]:
    return HookPayloadAdmission(
        PriorityConfig,
        str,
        Counter,
        Increment,
        transition_admission,
    )


def _request(value: str = "x") -> HookActivationRequest[str, Counter]:
    return HookActivationRequest(value, Counter(7))


def _completion(result: Graph.Result[HookGraphValue]) -> HookResult[str, Increment]:
    assert isinstance(result, Graph.CompletedResult)
    value = result.outputs["result"]
    assert type(value) is HookResult
    return cast(HookResult[str, Increment], value)


@pytest.mark.asyncio
async def test_hook_node_uses_the_assembly_plan_and_invokes_priorities_in_order() -> None:
    plan = _plan()
    runtime = SerialRuntime()
    request = _request()
    node = _node(runtime, plan)

    completion = _completion(await node.run(Graph.values(request=request)))

    assert completion == HookResult(
        "x1a2b",
        (Increment(1), Increment(2)),
    )
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2)
    assert tuple(
        call.config is config for call, config in zip(runtime.calls, (plan.p1.config, plan.p2.config), strict=True)
    ) == (True, True)
    assert tuple(call.request for call in runtime.calls) == ("x", "x1a")


@pytest.mark.asyncio
async def test_hook_node_reuses_the_same_assembly_plan_for_each_activation() -> None:
    plan = _plan("fixed-")
    runtime = SerialRuntime()
    node = _node(runtime, plan)

    first = _completion(await node.run(Graph.values(request=_request())))
    second = _completion(await node.run(Graph.values(request=_request())))

    assert first.value == "xfixed-1fixed-afixed-2fixed-b"
    assert second.value == first.value
    assert tuple(call.config for call in runtime.calls) == (
        plan.p1.config,
        plan.p2.config,
        plan.p1.config,
        plan.p2.config,
    )


@pytest.mark.asyncio
async def test_empty_priority_plans_still_make_one_invocation_per_fixed_priority_node() -> None:
    plan = HookPlan(
        HookPriorityPlan(PriorityConfig(1, ())),
        HookPriorityPlan(PriorityConfig(2, ())),
    )
    runtime = SerialRuntime()

    completion = _completion(await _node(runtime, plan).run(Graph.values(request=_request())))

    assert completion == HookResult("x")
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2)
    assert all(call.config.fragments == () for call in runtime.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_type", [SerialRuntime, ParallelRuntime], ids=["serial", "parallel"])
async def test_runtime_owns_internal_serial_or_parallel_handler_execution(
    runtime_type: type[SerialRuntime] | type[ParallelRuntime],
) -> None:
    runtime = runtime_type()

    completion = _completion(await _node(runtime).run(Graph.values(request=_request())))

    assert completion.value == "x1a2b"
    assert completion.commands == (Increment(1), Increment(2))
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2)


@pytest.mark.asyncio
async def test_hook_preserves_stage_command_order_and_duplicates() -> None:
    runtime = DuplicateCommandRuntime()

    completion = _completion(await _node(runtime).run(Graph.values(request=_request())))

    assert completion.value == "x"
    assert completion.commands == (
        Increment(1),
        Increment(1),
        Increment(2),
        Increment(2),
    )
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2)


@pytest.mark.asyncio
async def test_concrete_transition_admission_checks_every_priority_transition() -> None:
    transition = RecordingTransitionAdmission()
    runtime = SerialRuntime()
    node = HookNode(
        _slot(),
        _plan(),
        runtime,
        _admission(transition),
    )

    completion = _completion(await node.run(Graph.values(request=_request())))

    assert completion.value == "x1a2b"
    assert tuple(call.request.value for call in transition.calls) == (
        "x",
        "x1a",
    )
    assert tuple(call.result.value for call in transition.calls) == (
        "x1a",
        "x1a2b",
    )
    assert tuple(call.result.commands for call in transition.calls) == (
        (Increment(1),),
        (Increment(2),),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_rank", [1, 2])
async def test_transition_admission_failure_stops_before_the_next_priority(reject_rank: int) -> None:
    transition = RecordingTransitionAdmission(reject_rank)
    runtime = SerialRuntime()
    node = HookNode(
        _slot(),
        _plan(),
        runtime,
        _admission(transition),
    )

    with pytest.raises(RuntimeError) as raised:
        await node.run(Graph.values(request=_request()))

    assert raised.value is transition.failure
    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, reject_rank + 1))
    assert len(transition.calls) == reject_rank


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_rank", "expected_ranks"),
    [
        (1, (1,)),
        (2, (1, 2)),
    ],
)
async def test_invocation_failure_stops_at_the_failing_priority_without_hook_retry(
    failure_rank: int,
    expected_ranks: tuple[int, ...],
) -> None:
    runtime = FailingRuntime(failure_rank)

    with pytest.raises(RuntimeError, match="invocation failed"):
        await _node(runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == expected_ranks


@pytest.mark.asyncio
async def test_invocation_cancellation_propagates_without_running_later_priorities() -> None:
    runtime = CancellingRuntime()

    with pytest.raises(asyncio.CancelledError, match="invocation cancelled"):
        await _node(runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == (1,)


@pytest.mark.asyncio
async def test_hook_port_does_not_translate_an_invocation_type_error() -> None:
    runtime = RaisingTypeErrorRuntime()

    with pytest.raises(InvocationTypeError) as raised:
        await _node(runtime).run(Graph.values(request=_request()))

    assert raised.value is runtime.error
    assert tuple(call.config.rank for call in runtime.calls) == (1,)


@pytest.mark.asyncio
async def test_hook_port_preserves_an_invocation_boundary_error() -> None:
    runtime = RaisingBoundaryErrorRuntime()

    with pytest.raises(InvocationBoundaryError) as raised:
        await _node(runtime).run(Graph.values(request=_request()))

    assert raised.value is runtime.error
    assert tuple(call.config.rank for call in runtime.calls) == (1,)


def test_hook_node_rejects_an_invalid_direct_plan_before_graph_assembly() -> None:
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="HookPlan"):
        HookNode(
            _slot(),
            cast(HookPlan[PriorityConfig], object()),
            runtime,
            _admission(),
        )

    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_request", "field"),
    [
        (HookActivationRequest(cast(str, object()), Counter(7)), "value"),
        (HookActivationRequest("x", cast(Counter, object())), "state"),
    ],
)
async def test_hook_node_rejects_invalid_initial_request_payloads_before_invocation(
    input_request: HookActivationRequest[str, Counter],
    field: str,
) -> None:
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match=f"hook {field} has an unexpected payload type"):
        await _node(runtime).run(Graph.values(request=input_request))

    assert runtime.calls == []


@pytest.mark.parametrize("invalid_rank", [1, 2])
def test_hook_node_rejects_invalid_priority_config_during_assembly(invalid_rank: int) -> None:
    runtime = SerialRuntime()
    priorities = [
        HookPriorityPlan(PriorityConfig(1, ())),
        HookPriorityPlan(PriorityConfig(2, ())),
    ]
    priorities[invalid_rank - 1] = HookPriorityPlan(cast(PriorityConfig, object()))
    plan = HookPlan(priorities[0], priorities[1])

    with pytest.raises(HookContractError, match=f"hook P{invalid_rank} config has an unexpected payload type"):
        HookNode(_slot(), plan, runtime, _admission())

    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_rank", [1, 2])
async def test_internal_port_rejects_invalid_invocation_results(invalid_rank: int) -> None:
    runtime = InvalidResultRuntime(invalid_rank)
    node = _node(runtime)

    with pytest.raises(HookContractError, match="HookStageResult"):
        await node.run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, invalid_rank + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_rank", [1, 2])
async def test_internal_port_rejects_invalid_stage_value_before_next_priority(invalid_rank: int) -> None:
    runtime = InvalidStageValueRuntime(invalid_rank)

    with pytest.raises(HookContractError, match="hook value has an unexpected payload type"):
        await _node(runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, invalid_rank + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_rank", [1, 2])
async def test_internal_port_rejects_invalid_stage_command_before_next_priority(invalid_rank: int) -> None:
    runtime = InvalidStageCommandRuntime(invalid_rank)

    with pytest.raises(HookContractError, match="hook command has an unexpected payload type"):
        await _node(runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, invalid_rank + 1))


@pytest.mark.asyncio
async def test_internal_port_rejects_final_result_from_invocation() -> None:
    runtime = FinalResultRuntime()

    with pytest.raises(HookContractError, match="HookStageResult"):
        await _node(runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == (1,)


@pytest.mark.asyncio
async def test_hook_node_composes_as_a_nested_graph() -> None:
    runtime = SerialRuntime()
    hook = _node(runtime)
    assert type(hook.payload_admission) is HookPayloadAdmission
    result_ref = hook.output_ref("p2", "result")
    assert result_ref.node_id == GraphNodeId("p2")
    assert result_ref.output_name == "result"
    parent = Graph[HookGraphValue]("react.parent")
    request_type = cast(type[HookGraphValue], HookActivationRequest)
    parent.add_node(
        "hook",
        hook,
        inputs={"request": Graph.graph_input("request", request_type)},
    )
    parent_result_ref = parent.output_ref("hook", "result")
    assert result_ref.descriptor is parent_result_ref.descriptor
    parent.set_outputs({"result": parent_result_ref})

    completion = _completion(await parent.run(Graph.values(request=_request())))

    assert completion.value == "x1a2b"
    with pytest.raises(GraphValidationError, match="immutable"):
        hook.set_outputs({"result": Graph.node_output("p2", "result")})


@pytest.mark.asyncio
async def test_hook_completion_exports_the_originating_node_route() -> None:
    hook = _node(SerialRuntime())
    parent = Graph[HookGraphValue]("hook.route.parent")
    request_type = cast(type[HookGraphValue], HookActivationRequest)
    parent.add_node(
        "hook",
        hook,
        inputs={"request": Graph.graph_input("request", request_type)},
    )
    parent.add_edge("hook", "origin", Graph.END)
    parent.set_outputs({"result": Graph.node_output("hook", "result")})

    request = HookActivationRequest("x", Counter(1), GraphNodeId("origin"))
    result = await parent.run(Graph.values(request=request))
    assert isinstance(result, Graph.CompletedResult)
    completion = _completion(result)

    assert completion.value == "x1a2b"
    assert result.state.completion_route == "origin"


@pytest.mark.asyncio
async def test_parent_can_embed_hooks_whose_legacy_ids_would_collide() -> None:
    plan = _plan()
    runtime = SerialRuntime()
    left = HookNode(
        HookSlotId(GraphDefinitionId("a"), GraphDefinitionVersion(1), GraphNodeId("b.hook.c")),
        plan,
        runtime,
        _admission(),
    )
    right = HookNode(
        HookSlotId(GraphDefinitionId("a.hook.b"), GraphDefinitionVersion(1), GraphNodeId("c")),
        plan,
        runtime,
        _admission(),
    )
    parent = Graph[HookGraphValue]("collision.parent")
    request_type = cast(type[HookGraphValue], HookActivationRequest)
    request_input = Graph.graph_input("request", request_type)
    parent.add_node("left", left, inputs={"request": request_input})
    parent.add_node("right", right, inputs={"request": request_input})
    parent.set_outputs(
        {
            "left": Graph.node_output("left", "result"),
            "right": Graph.node_output("right", "result"),
        }
    )
    parent.add_join(("left", "right"), Graph.END)

    result = await parent.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["left"] == HookResult("x1a2b", (Increment(1), Increment(2)))
    assert result.outputs["right"] == HookResult("x1a2b", (Increment(1), Increment(2)))


def test_hook_node_rejects_missing_required_assembly_capabilities() -> None:
    plan = _plan()
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="HookSlotId"):
        HookNode[PriorityConfig, str, Counter, Increment](cast(HookSlotId, object()), plan, runtime, _admission())
    with pytest.raises(HookContractError, match="HookPlan"):
        HookNode[PriorityConfig, str, Counter, Increment](_slot(), None, runtime, _admission())
    with pytest.raises(HookContractError, match="invocation capability"):
        HookNode[PriorityConfig, str, Counter, Increment](_slot(), plan, None, _admission())


def test_hook_node_rejects_non_callable_invocation_capabilities() -> None:
    with pytest.raises(HookContractError, match="invocation capability"):
        HookNode[PriorityConfig, str, Counter, Increment](
            _slot(),
            _plan(),
            cast(
                Invocation[
                    HookInvocationRequest[PriorityConfig, str],
                    HookStageResult[str, Increment],
                ],
                object(),
            ),
            _admission(),
        )
    with pytest.raises(HookContractError, match="invocation capability"):
        HookNode[PriorityConfig, str, Counter, Increment](
            _slot(),
            _plan(),
            cast(
                Invocation[
                    HookInvocationRequest[PriorityConfig, str],
                    HookStageResult[str, Increment],
                ],
                NonCallableInvocation(),
            ),
            _admission(),
        )


class _BindingConfig:
    def __init__(self, result: object) -> None:
        self.result = result

    def bind(self, _selector: object, /) -> object:
        return self.result


def _priority_node() -> object:
    graph = cast(
        _InspectableHookGraph,
        _node(
            cast(
                Invocation[HookInvocationRequest[PriorityConfig, str], HookStageResult[str, Increment]],
                SerialRuntime(),
            )
        ),
    )
    state = cast(_HookBuilderState, object.__getattribute__(graph, "_builder_state"))
    candidate = cast(_HookBuilderNode, state.nodes[0])
    invoker = cast(_HookInvoker, candidate.invoker)
    return invoker.operation


def _priority_runtime(
    node: object,
    config: Config | None,
    /,
) -> tuple[
    HookPriorityPlan[PriorityConfig],
    HookPort[PriorityConfig, str, Counter, Increment],
]:
    runtime = cast(
        Callable[
            [Config | None],
            tuple[
                HookPriorityPlan[PriorityConfig],
                HookPort[PriorityConfig, str, Counter, Increment],
            ],
        ],
        object.__getattribute__(cast(_PriorityNodeView, node), "_runtime"),
    )
    return runtime(config)


def test_priority_node_runtime_rejects_invalid_projection_and_contract() -> None:
    node = _priority_node()
    with pytest.raises(HookContractError, match="invalid projection"):
        _priority_runtime(node, cast(Config, _BindingConfig(object())))

    wrong_slot = _slot("other")
    key = ConfigSnapshotKey(GraphDefinitionId("react"), GraphDefinitionVersion(1), 1)
    wrong = HookPriorityConfig(
        key,
        wrong_slot,
        _plan().p1,
        cast(
            Invocation[HookInvocationRequest[PriorityConfig, str], HookStageResult[str, Increment]],
            SerialRuntime(),
        ),
        _admission(),
    )
    with pytest.raises(HookContractError, match="compiled Hook contract"):
        _priority_runtime(node, cast(Config, _BindingConfig(wrong)))


def test_hook_node_rejects_a_malformed_assembly_snapshot_key() -> None:
    with pytest.raises(HookContractError, match="assembly snapshot key"):
        HookNode(
            _slot(),
            _plan(),
            SerialRuntime(),
            _admission(),
            assembly_snapshot_key=cast(ConfigSnapshotKey, object()),
        )


def test_hook_node_rejects_an_invalid_payload_admission() -> None:
    with pytest.raises(HookContractError, match="payload admission contract"):
        HookNode[PriorityConfig, str, Counter, Increment](
            _slot(),
            _plan(),
            SerialRuntime(),
            cast(HookPayloadAdmission[PriorityConfig, str, Counter, Increment], object()),
        )


def test_plan_and_result_validate_their_minimal_nominal_boundaries() -> None:
    priority = HookPriorityPlan(PriorityConfig(1, ("value",)))
    plan = HookPlan(priority, priority)
    stage = HookStageResult("stage", (Increment(1),))
    result = HookResult("result", (Increment(2),))

    assert not hasattr(stage, "state")
    assert not hasattr(result, "state")

    with pytest.raises(FrozenInstanceError):
        priority.config = PriorityConfig(1, ())  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.p1 = priority  # type: ignore[misc]
    with pytest.raises(TypeError, match="HookPriorityPlan"):
        HookPlan(cast(HookPriorityPlan[PriorityConfig], object()), priority)
    invocation = HookInvocationRequest(PriorityConfig(1, ()), "payload")
    assert invocation.hook_config.rank == 1
    assert invocation.payload == "payload"
    with pytest.raises(FrozenInstanceError):
        stage.value = "replacement"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.value = "replacement"  # type: ignore[misc]
    with pytest.raises(TypeError, match="tuple"):
        HookStageResult("value", cast(tuple[Increment, ...], []))
    with pytest.raises(TypeError, match="tuple"):
        HookResult("value", cast(tuple[Increment, ...], []))


def test_request_and_result_reject_noncanonical_node_ids() -> None:
    invalid_node_id = cast(GraphNodeId, "bad\nnode")
    with pytest.raises(HookContractError, match="canonical GraphNodeId"):
        HookActivationRequest("value", Counter(1), invalid_node_id)
    with pytest.raises(HookContractError, match="canonical GraphNodeId"):
        HookResult("value", node_id=invalid_node_id)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("priority config", cast(type[PriorityConfig], object())),
        ("value", cast(type[str], object())),
        ("state", cast(type[Counter], object())),
        ("command", cast(type[Increment], object())),
    ],
)
def test_payload_admission_rejects_erased_descriptor_types(field: str, replacement: type[object]) -> None:
    types: list[type[object]] = [PriorityConfig, str, Counter, Increment]
    index = ("priority config", "value", "state", "command").index(field)
    types[index] = replacement

    with pytest.raises(HookContractError, match=f"hook {field} type must be one concrete nominal class"):
        HookPayloadAdmission(*types)  # type: ignore[arg-type]


def test_payload_admission_rejects_invalid_transition_admission_contracts() -> None:
    with pytest.raises(HookContractError, match="HookTransitionAdmission"):
        _admission(cast(HookTransitionAdmission[str, Counter, Increment], object()))
    with pytest.raises(HookContractError, match="HookTransitionAdmission"):
        _admission(
            cast(
                HookTransitionAdmission[str, Counter, Increment],
                NonCallableTransitionAdmission(),
            )
        )


def test_payload_admission_rejects_malformed_nominal_result_objects() -> None:
    admission = _admission()
    malformed_stage = cast(HookStageResult[str, Increment], object.__new__(HookStageResult))
    object.__setattr__(malformed_stage, "value", "value")
    object.__setattr__(malformed_stage, "commands", [])
    malformed_result = cast(HookResult[str, Increment], object.__new__(HookResult))
    object.__setattr__(malformed_result, "value", "value")
    object.__setattr__(malformed_result, "commands", [])

    with pytest.raises(HookContractError, match="hook stage result commands must be a tuple"):
        admission.admit_stage_result(malformed_stage)
    with pytest.raises(HookContractError, match="hook result commands must be a tuple"):
        admission.admit_result(malformed_result)


def test_payload_admission_rejects_wrong_nominal_wrappers() -> None:
    admission = _admission()

    with pytest.raises(HookContractError, match="HookActivationRequest"):
        admission.admit_request(cast(HookActivationRequest[str, Counter], object()))
    with pytest.raises(HookContractError, match="HookInvocationRequest"):
        admission.admit_invocation_request(cast(HookInvocationRequest[PriorityConfig, str], object()))
    with pytest.raises(HookContractError, match="HookStageResult"):
        admission.admit_stage_result(cast(HookStageResult[str, Increment], object()))
    with pytest.raises(HookContractError, match="HookResult"):
        admission.admit_result(cast(HookResult[str, Increment], object()))


def test_payload_admission_rejects_wrong_priority_wrapper() -> None:
    admission = _admission()
    priority = HookPriorityPlan(PriorityConfig(1, ()))
    malformed_plan = cast(HookPlan[PriorityConfig], object.__new__(HookPlan))
    object.__setattr__(malformed_plan, "p1", object())
    object.__setattr__(malformed_plan, "p2", priority)

    with pytest.raises(HookContractError, match="hook plan P1 must be a HookPriorityPlan"):
        admission.admit_plan(malformed_plan)


def test_slot_validates_compile_time_coordinates() -> None:
    with pytest.raises(ValueError, match="definition id"):
        HookSlotId(GraphDefinitionId(""), GraphDefinitionVersion(1), GraphNodeId("node"))
    with pytest.raises(ValueError, match="version"):
        HookSlotId(GraphDefinitionId("graph"), GraphDefinitionVersion(0), GraphNodeId("node"))
    with pytest.raises(ValueError, match="node id"):
        HookSlotId(GraphDefinitionId("graph"), GraphDefinitionVersion(1), GraphNodeId(""))
    with pytest.raises(ValueError, match="stage"):
        HookSlotId(
            GraphDefinitionId("graph"),
            GraphDefinitionVersion(1),
            GraphNodeId("node"),
            cast(HookStage, 1),
        )


def test_hook_priorities_are_exactly_p1_and_p2() -> None:
    assert tuple(HookPriority) == (HookPriority.P1, HookPriority.P2)


@pytest.mark.parametrize(
    ("definition_id", "node_id", "expected"),
    [
        (
            "parent.graph",
            "node",
            "12:mote.hook.v112:parent.graph4:node10:after_node",
        ),
        (
            "a",
            "b.hook.c",
            "12:mote.hook.v11:a8:b.hook.c10:after_node",
        ),
        (
            "a.hook.b",
            "c",
            "12:mote.hook.v18:a.hook.b1:c10:after_node",
        ),
        (
            "父图:\u03b1",
            "节点 空",
            "12:mote.hook.v14:父图:\u03b14:节点 空10:after_node",
        ),
        (
            "parent graph",
            "node slot",
            "12:mote.hook.v112:parent graph9:node slot10:after_node",
        ),
    ],
)
def test_hook_definition_id_v1_exact_vectors(definition_id: str, node_id: str, expected: str) -> None:
    slot = HookSlotId(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(7),
        GraphNodeId(node_id),
    )

    assert hook_definition_id(slot) == GraphDefinitionId(expected)


def test_hook_definition_id_separates_old_delimiter_collisions() -> None:
    left = hook_definition_id(HookSlotId(GraphDefinitionId("a"), GraphDefinitionVersion(1), GraphNodeId("b.hook.c")))
    right = hook_definition_id(HookSlotId(GraphDefinitionId("a.hook.b"), GraphDefinitionVersion(1), GraphNodeId("c")))

    assert left != right


def test_hook_definition_id_does_not_encode_definition_version() -> None:
    first = HookSlotId(GraphDefinitionId("parent"), GraphDefinitionVersion(1), GraphNodeId("node"))
    second = HookSlotId(GraphDefinitionId("parent"), GraphDefinitionVersion(2), GraphNodeId("node"))

    assert hook_definition_id(first) == hook_definition_id(second)


def test_hook_definition_identity_rejects_invalid_inputs() -> None:
    with pytest.raises(TypeError, match="HookSlotId"):
        hook_definition_id(cast(HookSlotId, object()))


@pytest.mark.parametrize(
    ("definition_id", "node_id", "message"),
    [
        (" parent", "node", "definition id"),
        ("parent ", "node", "definition id"),
        ("parent", " node", "node id"),
        ("parent", "node ", "node id"),
    ],
)
def test_hook_slot_rejects_identity_boundary_whitespace(
    definition_id: str,
    node_id: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        HookSlotId(
            GraphDefinitionId(definition_id),
            GraphDefinitionVersion(1),
            GraphNodeId(node_id),
        )


def test_hook_node_is_the_only_package_level_api_and_external_port_spi_is_removed() -> None:
    assert hooks_package.__all__ == ["HookNode"]
    assert "HookPort" not in hooks_contract.__all__
    assert not hasattr(hooks_contract, "HookPort")
    assert "HookInvocation" not in hooks_contract.__all__
    assert not hasattr(hooks_contract, "HookInvocation")
    assert invocation_package.__all__ == ["Invocation"]
    assert Invocation.__module__ == "mote_kernel.invocation"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("mote_kernel.hooks.manager")


def test_hook_node_slot_is_read_only() -> None:
    node = _node(SerialRuntime())
    original = node.slot

    with pytest.raises(AttributeError):
        node.slot = _slot("replacement")  # type: ignore[misc]

    assert node.slot is original


def test_payload_admission_admits_only_the_exact_plan_wrapper() -> None:
    admission = _admission()
    priority = HookPriorityPlan(PriorityConfig(1, ("one",)))
    plan = HookPlan(priority, priority)

    assert admission.admit_plan(plan) is plan

    with pytest.raises(HookContractError, match="HookPlan"):
        admission.admit_plan(cast(HookPlan[PriorityConfig], object()))
    with pytest.raises(HookContractError, match="HookPlan"):
        admission.admit_plan(PlanSubclass(priority, priority))


@pytest.mark.parametrize("priority_name", ["P1", "P2"])
def test_payload_admission_checks_each_priority_wrapper_and_config(
    priority_name: str,
) -> None:
    admission = _admission()
    priority = HookPriorityPlan(PriorityConfig(1, ()))
    priorities: list[HookPriorityPlan[PriorityConfig] | object] = [priority, priority]
    priorities[int(priority_name[1]) - 1] = object()
    malformed = cast(HookPlan[PriorityConfig], object.__new__(HookPlan))
    object.__setattr__(malformed, "p1", priorities[0])
    object.__setattr__(malformed, "p2", priorities[1])

    with pytest.raises(HookContractError, match=f"hook plan {priority_name} must be a HookPriorityPlan"):
        admission.admit_plan(malformed)

    priorities = [priority, priority]
    priorities[int(priority_name[1]) - 1] = HookPriorityPlan(cast(PriorityConfig, object()))
    invalid_config_plan = HookPlan(
        cast(HookPriorityPlan[PriorityConfig], priorities[0]),
        cast(HookPriorityPlan[PriorityConfig], priorities[1]),
    )
    with pytest.raises(HookContractError, match=f"hook {priority_name} config has an unexpected"):
        admission.admit_plan(invalid_config_plan)


def test_payload_admission_checks_exact_request_and_invocation_request_boundaries() -> None:
    admission = _admission()
    request = _request()
    invocation_request = HookInvocationRequest(PriorityConfig(1, ()), "payload")

    assert admission.admit_request(request) is request
    assert admission.admit_invocation_request(invocation_request) is invocation_request

    with pytest.raises(HookContractError, match="HookActivationRequest"):
        admission.admit_request(cast(HookActivationRequest[str, Counter], object()))
    with pytest.raises(HookContractError, match="HookActivationRequest"):
        admission.admit_request(RequestSubclass("x", Counter(1)))
    with pytest.raises(HookContractError, match="hook value has an unexpected"):
        admission.admit_request(HookActivationRequest(cast(str, object()), Counter(1)))
    with pytest.raises(HookContractError, match="hook state has an unexpected"):
        admission.admit_request(HookActivationRequest("x", cast(Counter, object())))
    with pytest.raises(HookContractError, match="HookInvocationRequest"):
        admission.admit_invocation_request(cast(HookInvocationRequest[PriorityConfig, str], object()))
    with pytest.raises(HookContractError, match="HookInvocationRequest"):
        admission.admit_invocation_request(InvocationRequestSubclass(PriorityConfig(1, ()), "payload"))
    with pytest.raises(HookContractError, match="priority config has an unexpected"):
        admission.admit_invocation_request(HookInvocationRequest(cast(PriorityConfig, object()), request.value))
    with pytest.raises(HookContractError, match="hook payload has an unexpected"):
        admission.admit_invocation_request(HookInvocationRequest(PriorityConfig(1, ()), cast(str, object())))

    malformed = cast(HookInvocationRequest[PriorityConfig, str], object.__new__(HookInvocationRequest))
    object.__setattr__(malformed, "hook_config", PriorityConfig(1, ()))
    object.__setattr__(malformed, "payload", "payload")
    object.__setattr__(malformed, "config_cursor", cast(GraphConfigCursor, object()))
    with pytest.raises(HookContractError, match="config_cursor"):
        admission.admit_invocation_request(malformed)
    with pytest.raises(TypeError, match="config_cursor"):
        HookInvocationRequest(PriorityConfig(1, ()), "payload", cast(GraphConfigCursor, object()))


@pytest.mark.parametrize("command_index", [0, 1, 2])
def test_payload_admission_checks_every_stage_command_element(command_index: int) -> None:
    admission = _admission()
    commands: list[Increment | object] = [Increment(1), Increment(2), Increment(3)]
    commands[command_index] = object()
    malformed = HookStageResult("value", cast(tuple[Increment, ...], tuple(commands)))

    with pytest.raises(HookContractError, match="hook command has an unexpected"):
        admission.admit_stage_result(malformed)


def test_payload_admission_checks_exact_stage_and_final_result_wrappers() -> None:
    admission = _admission()
    stage = HookStageResult("value", (Increment(1),))
    result = HookResult("value", (Increment(2),), GraphNodeId("origin"))

    assert admission.admit_stage_result(stage) is stage
    assert admission.admit_result(result) is result

    with pytest.raises(HookContractError, match="HookStageResult"):
        admission.admit_stage_result(cast(HookStageResult[str, Increment], object()))
    with pytest.raises(HookContractError, match="HookStageResult"):
        admission.admit_stage_result(StageResultSubclass("value"))
    with pytest.raises(HookContractError, match="hook value has an unexpected"):
        admission.admit_stage_result(HookStageResult(cast(str, object())))
    with pytest.raises(HookContractError, match="HookResult"):
        admission.admit_result(cast(HookResult[str, Increment], object()))
    with pytest.raises(HookContractError, match="HookResult"):
        admission.admit_result(ResultSubclass("value"))
    with pytest.raises(HookContractError, match="hook value has an unexpected"):
        admission.admit_result(HookResult(cast(str, object())))
    with pytest.raises(HookContractError, match="hook result commands must be a tuple"):
        malformed = cast(HookResult[str, Increment], object.__new__(HookResult))
        object.__setattr__(malformed, "value", "value")
        object.__setattr__(malformed, "commands", [])
        object.__setattr__(malformed, "node_id", None)
        admission.admit_result(malformed)


def test_optional_transition_admission_is_a_noop_or_forwards_exact_objects() -> None:
    request = _request()
    stage = HookStageResult("next", (Increment(1),))

    _admission().admit_transition(request, stage)

    transition = RecordingTransitionAdmission()
    _admission(transition).admit_transition(request, stage)
    assert len(transition.calls) == 1
    assert transition.calls[0].request is request
    assert transition.calls[0].result is stage


def test_transition_admission_preserves_its_exception_instance() -> None:
    transition = RecordingTransitionAdmission(reject_rank=1)
    request = _request()
    stage = HookStageResult("next")

    with pytest.raises(RuntimeError) as raised:
        _admission(transition).admit_transition(request, stage)

    assert raised.value is transition.failure


@pytest.mark.asyncio
async def test_hook_port_forwards_exact_plan_request_and_transition_objects() -> None:
    transition = RecordingTransitionAdmission()
    runtime = SerialRuntime()
    request = _request("port-")
    plan = HookPlan(
        HookPriorityPlan(PriorityConfig(1, ("one",))),
        HookPriorityPlan(PriorityConfig(2, ())),
    )
    port = HookPort(_admission(transition), runtime)

    result = await port.execute(plan.p1, request, None)

    assert result is not None
    assert runtime.calls[0].config is plan.p1.config
    assert runtime.calls[0].request == request.value
    assert transition.calls[0].request is request
    assert transition.calls[0].result is result


@pytest.mark.asyncio
async def test_hook_port_rejects_invalid_plan_or_request_before_invocation() -> None:
    runtime = SerialRuntime()
    port = HookPort(_admission(), runtime)
    valid_request = _request()

    with pytest.raises(HookContractError, match="priority config has an unexpected"):
        await port.execute(HookPriorityPlan(cast(PriorityConfig, object())), valid_request, None)
    with pytest.raises(HookContractError, match="hook value has an unexpected"):
        await port.execute(
            HookPriorityPlan(PriorityConfig(1, ())),
            HookActivationRequest(cast(str, object()), Counter(1)),
            None,
        )

    assert runtime.calls == []


@pytest.mark.asyncio
async def test_hook_port_marks_invocation_result_admission_failures_without_retry() -> None:
    runtime = InvalidResultRuntime(1)
    port = HookPort(_admission(), runtime)

    with pytest.raises(HookContractError, match="HookStageResult") as raised:
        await port.execute(HookPriorityPlan(PriorityConfig(1, ())), _request(), None)

    assert isinstance(raised.value.__cause__, InvocationBoundaryAdmissionError)
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_sync_invocation_capability_fails_at_the_async_boundary() -> None:
    runtime = SyncRuntime()
    invocation = cast(
        Invocation[
            HookInvocationRequest[PriorityConfig, str],
            HookStageResult[str, Increment],
        ],
        runtime,
    )

    with pytest.raises(TypeError, match="await"):
        await _node(invocation).run(Graph.values(request=_request()))

    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_hook_runs_keep_progress_and_state_isolated() -> None:
    runtime = YieldingRuntime()
    node = _node(runtime)
    first_request = HookActivationRequest("first", Counter(1), GraphNodeId("first"))
    second_request = HookActivationRequest("second", Counter(2), GraphNodeId("second"))

    first_result, second_result = await asyncio.gather(
        node.run(Graph.values(request=first_request)),
        node.run(Graph.values(request=second_request)),
    )

    first = _completion(first_result)
    second = _completion(second_result)
    assert first == HookResult("first1a2b", (Increment(1), Increment(2)), GraphNodeId("first"))
    assert second == HookResult("second1a2b", (Increment(1), Increment(2)), GraphNodeId("second"))
    assert {call.request for call in runtime.calls} == {"first", "first1a", "second", "second1a"}


@pytest.mark.asyncio
async def test_hook_without_origin_node_id_completes_without_an_exported_route() -> None:
    node = _node(SerialRuntime())

    result = await node.run(Graph.values(request=_request()))

    assert isinstance(result, Graph.CompletedResult)
    assert _completion(result).node_id is None
    assert result.state.completion_route is None


@pytest.mark.asyncio
async def test_hook_nested_completion_route_selects_the_parent_declared_branch() -> None:
    hook = _node(SerialRuntime())
    parent = Graph[HookGraphValue]("hook.route.conditional")
    request_type = cast(type[HookGraphValue], HookActivationRequest)
    parent.add_node("hook", hook, inputs={"request": Graph.graph_input("request", request_type)})
    visited: list[str] = []

    async def left(_values: Graph.Values[HookGraphValue]) -> Graph.Values[HookGraphValue]:
        visited.append("left")
        return Graph.values()

    async def right(_values: Graph.Values[HookGraphValue]) -> Graph.Values[HookGraphValue]:
        visited.append("right")
        return Graph.values()

    parent.add_node("left", left, inputs={}, outputs={})
    parent.add_node("right", right, inputs={}, outputs={})
    parent.add_edge("hook", "left", "left")
    parent.add_edge("hook", "right", "right")
    parent.add_edge("left", Graph.END)
    parent.add_edge("right", Graph.END)
    parent.set_outputs({"result": parent.output_ref("hook", "result")})

    result = await parent.run(Graph.values(request=HookActivationRequest("x", Counter(1), GraphNodeId("right"))))

    assert isinstance(result, Graph.CompletedResult)
    assert visited == ["right"]
    assert result.state.completion_route is None


@pytest.mark.asyncio
async def test_hook_nested_completion_rejects_an_undeclared_route() -> None:
    hook = _node(SerialRuntime())
    parent = Graph[HookGraphValue]("hook.route.unknown")
    request_type = cast(type[HookGraphValue], HookActivationRequest)
    parent.add_node("hook", hook, inputs={"request": Graph.graph_input("request", request_type)})
    parent.add_edge("hook", "known", Graph.END)
    parent.set_outputs({"result": parent.output_ref("hook", "result")})

    with pytest.raises(Graph.RoutingError, match="unknown conditional route"):
        await parent.run(Graph.values(request=HookActivationRequest("x", Counter(1), GraphNodeId("unknown"))))


def test_hook_graph_contains_only_p1_and_p2_and_exports_the_p2_result() -> None:
    hook = _node(SerialRuntime())

    assert not hasattr(hook, "result_output")
    output = hook.output_ref("p2", "result")
    assert output.node_id == GraphNodeId("p2")
    assert output.output_name == "result"
    assert output.descriptor is not None
    with pytest.raises(GraphValidationError, match="declared node"):
        hook.output_ref("plan", "result")
    with pytest.raises(GraphValidationError, match="declared node"):
        hook.output_ref("p3", "result")
    with pytest.raises(GraphValidationError, match="source output"):
        hook.output_ref("p2", " result ")


@pytest.mark.asyncio
async def test_hook_graph_rejects_all_builder_mutations_after_successful_compile() -> None:
    hook = _node(SerialRuntime())
    await hook.run(Graph.values(request=_request()))

    async def late(_values: Graph.Values[HookGraphValue]) -> Graph.Values[HookGraphValue]:
        return Graph.values()

    with pytest.raises(GraphValidationError, match="immutable"):
        hook.add_node("late", late, inputs={}, outputs={})
    with pytest.raises(GraphValidationError, match="immutable"):
        hook.add_edge("p2", Graph.END)
    with pytest.raises(GraphValidationError, match="immutable"):
        hook.add_join(("p1", "p2"), Graph.END)
    with pytest.raises(GraphValidationError, match="immutable"):
        hook.set_outputs({"result": Graph.node_output("p2", "result")})
    with pytest.raises(GraphValidationError, match="immutable"):
        hook.set_resume_codec("hook.test", 1, lambda values: b"", lambda payload: Graph.values())

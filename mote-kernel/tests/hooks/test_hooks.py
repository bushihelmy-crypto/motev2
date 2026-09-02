"""Deterministic tests for the minimal graph-facing HookNode."""

import asyncio
import importlib
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from typing import cast

import pytest

import mote_kernel.hooks as hooks_package
import mote_kernel.hooks.contract as hooks_contract
import mote_kernel.invocation as invocation_package
from mote_kernel.execution import Graph
from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import (
    HookConfigSource,
    HookContractError,
    HookGraphValue,
    HookInvocationRequest,
    HookPayloadAdmission,
    HookPlanLoader,
    HookRequest,
    HookResult,
    HookStageResult,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage, hook_definition_id
from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan, HookPriorityPlan
from mote_kernel.invocation import Invocation
from mote_kernel.state.graph_state import GraphDefinitionId, GraphDefinitionVersion, GraphNodeId


@dataclass(frozen=True, slots=True)
class Config:
    priorities: tuple[
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]


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
    request: HookRequest[str, Counter]


class ConfigSource:
    def __init__(self, config: Config) -> None:
        self.current = HookConfigSnapshot(config)
        self.calls = 0

    def snapshot(self) -> HookConfigSnapshot[Config]:
        self.calls += 1
        return self.current

    def replace(self, config: Config) -> None:
        self.current = HookConfigSnapshot(config)


class PlanLoader:
    def __init__(self) -> None:
        self.snapshots: list[HookConfigSnapshot[Config]] = []
        self.plans: list[HookPlan[PriorityConfig]] = []

    def load(self, snapshot: HookConfigSnapshot[Config], /) -> HookPlan[PriorityConfig]:
        self.snapshots.append(snapshot)
        first, second, third = snapshot.config.priorities
        plan = HookPlan(
            HookPriorityPlan(PriorityConfig(1, first)),
            HookPriorityPlan(PriorityConfig(2, second)),
            HookPriorityPlan(PriorityConfig(3, third)),
        )
        self.plans.append(plan)
        return plan


class InvalidConfigSource:
    def snapshot(self) -> HookConfigSnapshot[Config]:
        return cast(HookConfigSnapshot[Config], object())


class InvalidPlanLoader:
    def load(self, snapshot: HookConfigSnapshot[Config], /) -> HookPlan[PriorityConfig]:
        return cast(HookPlan[PriorityConfig], object())


class InvalidSnapshotPayloadSource:
    def snapshot(self) -> HookConfigSnapshot[Config]:
        return HookConfigSnapshot(cast(Config, object()))


class InvalidPriorityConfigLoader:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank

    def load(self, snapshot: HookConfigSnapshot[Config], /) -> HookPlan[PriorityConfig]:
        priority_plans = [
            HookPriorityPlan(PriorityConfig(1, snapshot.config.priorities[0])),
            HookPriorityPlan(PriorityConfig(2, snapshot.config.priorities[1])),
            HookPriorityPlan(PriorityConfig(3, snapshot.config.priorities[2])),
        ]
        priority_plans[self.invalid_rank - 1] = HookPriorityPlan(cast(PriorityConfig, object()))
        return HookPlan(*priority_plans)


class SerialRuntime:
    def __init__(self, after_first: Callable[[], None] | None = None) -> None:
        self.calls: list[InvocationCall] = []
        self.after_first = after_first

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        value = request.value
        for fragment in config.fragments:
            value = f"{value}{fragment}"
        if config.rank == 1 and self.after_first is not None:
            self.after_first()
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
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        fragments = await asyncio.gather(*(self._fragment(fragment) for fragment in config.fragments))
        commands = (Increment(config.rank),) if fragments else ()
        return HookStageResult(f"{request.value}{''.join(fragments)}", commands)


class FailingRuntime:
    def __init__(self, failure_rank: int) -> None:
        self.failure_rank = failure_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.failure_rank:
            raise RuntimeError("invocation failed")
        return HookStageResult(f"{request.value}{''.join(config.fragments)}")


class CancellingRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        raise asyncio.CancelledError("invocation cancelled")


class InvalidResultRuntime:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.invalid_rank:
            return cast(HookStageResult[str, Increment], object())
        return HookStageResult(f"{request.value}{''.join(config.fragments)}")


class FinalResultRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        return cast(HookStageResult[str, Increment], HookResult(request.value))


class DuplicateCommandRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        command = Increment(config.rank)
        return HookStageResult(request.value, (command, command))


class InvalidStageValueRuntime:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.invalid_rank:
            return HookStageResult(cast(str, object()))
        return HookStageResult(request.value)


class InvalidStageCommandRuntime:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        invocation_request: HookInvocationRequest[PriorityConfig, str, Counter],
        /,
    ) -> HookStageResult[str, Increment]:
        config = invocation_request.config
        request = invocation_request.request
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.invalid_rank:
            return HookStageResult(request.value, (cast(Increment, object()),))
        return HookStageResult(request.value)


class NonCallableSnapshotSource:
    snapshot = None


class NonCallablePlanLoader:
    load = None


class NonCallableInvocation:
    invoke = None


def _slot(node_id: str = "observe") -> HookSlotId:
    return HookSlotId(
        GraphDefinitionId("react"),
        GraphDefinitionVersion(1),
        GraphNodeId(node_id),
    )


def _config(prefix: str = "") -> Config:
    return Config(
        (
            (f"{prefix}1", f"{prefix}a"),
            (f"{prefix}2", f"{prefix}b"),
            (f"{prefix}3", f"{prefix}c"),
        )
    )


def _node(
    source: HookConfigSource[Config],
    loader: HookPlanLoader[Config, PriorityConfig],
    invocation: Invocation[
        HookInvocationRequest[PriorityConfig, str, Counter],
        HookStageResult[str, Increment],
    ],
) -> HookNode[Config, PriorityConfig, str, Counter, Increment]:
    return HookNode(_slot(), source, loader, invocation, _admission())


def _admission() -> HookPayloadAdmission[Config, PriorityConfig, str, Counter, Increment]:
    return HookPayloadAdmission(Config, PriorityConfig, str, Counter, Increment)


def _request(value: str = "x") -> HookRequest[str, Counter]:
    return HookRequest(value, Counter(7))


def _completion(result: Graph.Result[HookGraphValue]) -> HookResult[str, Increment]:
    assert isinstance(result, Graph.CompletedResult)
    value = result.outputs["result"]
    assert type(value) is HookResult
    return cast(HookResult[str, Increment], value)


@pytest.mark.asyncio
async def test_hook_node_reads_one_snapshot_builds_one_plan_and_invokes_priorities_in_order() -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = SerialRuntime()
    request = _request()
    node = _node(source, loader, runtime)

    completion = _completion(await node.run(Graph.values(request=request)))

    assert completion == HookResult(
        "x1a2b3c",
        (Increment(1), Increment(2), Increment(3)),
    )
    assert source.calls == 1
    assert loader.snapshots == [source.current]
    assert len(loader.plans) == 1
    plan = loader.plans[0]
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2, 3)
    assert tuple(
        call.config is config
        for call, config in zip(runtime.calls, (plan.p1.config, plan.p2.config, plan.p3.config), strict=True)
    ) == (True, True, True)
    assert tuple(call.request.value for call in runtime.calls) == ("x", "x1a", "x1a2b")
    assert all(call.request.state is request.state for call in runtime.calls)


@pytest.mark.asyncio
async def test_config_update_during_hook_node_only_affects_the_next_invocation() -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = SerialRuntime(lambda: source.replace(_config("new-")))
    node = _node(source, loader, runtime)

    first = _completion(await node.run(Graph.values(request=_request())))
    second = _completion(await node.run(Graph.values(request=_request())))

    assert first.value == "x1a2b3c"
    assert second.value == "xnew-1new-anew-2new-bnew-3new-c"
    assert source.calls == 2
    assert len(loader.plans) == 2
    assert loader.plans[0] is not loader.plans[1]


@pytest.mark.asyncio
async def test_empty_priority_plans_still_make_one_invocation_per_fixed_priority_node() -> None:
    source = ConfigSource(Config(((), (), ())))
    loader = PlanLoader()
    runtime = SerialRuntime()

    completion = _completion(await _node(source, loader, runtime).run(Graph.values(request=_request())))

    assert completion == HookResult("x")
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2, 3)
    assert all(call.config.fragments == () for call in runtime.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_type", [SerialRuntime, ParallelRuntime], ids=["serial", "parallel"])
async def test_runtime_owns_internal_serial_or_parallel_handler_execution(
    runtime_type: type[SerialRuntime] | type[ParallelRuntime],
) -> None:
    runtime = runtime_type()

    completion = _completion(
        await _node(ConfigSource(_config()), PlanLoader(), runtime).run(Graph.values(request=_request()))
    )

    assert completion.value == "x1a2b3c"
    assert completion.commands == (Increment(1), Increment(2), Increment(3))
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2, 3)


@pytest.mark.asyncio
async def test_hook_preserves_stage_command_order_and_duplicates() -> None:
    runtime = DuplicateCommandRuntime()

    completion = _completion(
        await _node(ConfigSource(_config()), PlanLoader(), runtime).run(Graph.values(request=_request()))
    )

    assert completion.value == "x"
    assert completion.commands == (
        Increment(1),
        Increment(1),
        Increment(2),
        Increment(2),
        Increment(3),
        Increment(3),
    )
    assert tuple(call.config.rank for call in runtime.calls) == (1, 2, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_rank", "expected_ranks"),
    [
        (1, (1,)),
        (2, (1, 2)),
        (3, (1, 2, 3)),
    ],
)
async def test_invocation_failure_stops_at_the_failing_priority_without_hook_retry(
    failure_rank: int,
    expected_ranks: tuple[int, ...],
) -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = FailingRuntime(failure_rank)

    with pytest.raises(RuntimeError, match="invocation failed"):
        await _node(source, loader, runtime).run(Graph.values(request=_request()))

    assert source.calls == 1
    assert len(loader.plans) == 1
    assert tuple(call.config.rank for call in runtime.calls) == expected_ranks


@pytest.mark.asyncio
async def test_invocation_cancellation_propagates_without_running_later_priorities() -> None:
    runtime = CancellingRuntime()

    with pytest.raises(asyncio.CancelledError, match="invocation cancelled"):
        await _node(ConfigSource(_config()), PlanLoader(), runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == (1,)


@pytest.mark.asyncio
async def test_hook_node_rejects_an_invalid_config_snapshot_before_loading_a_plan() -> None:
    loader = PlanLoader()
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="HookConfigSnapshot"):
        await _node(InvalidConfigSource(), loader, runtime).run(Graph.values(request=_request()))

    assert loader.snapshots == []
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_hook_node_rejects_an_invalid_loaded_plan_before_invocation() -> None:
    source = ConfigSource(_config())
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="HookPlan"):
        await _node(source, InvalidPlanLoader(), runtime).run(Graph.values(request=_request()))

    assert source.calls == 1
    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_request", "field"),
    [
        (HookRequest(cast(str, object()), Counter(7)), "value"),
        (HookRequest("x", cast(Counter, object())), "state"),
    ],
)
async def test_hook_node_rejects_invalid_initial_request_payloads_before_snapshot(
    input_request: HookRequest[str, Counter],
    field: str,
) -> None:
    source = ConfigSource(_config())
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match=f"hook {field} has an unexpected payload type"):
        await _node(source, PlanLoader(), runtime).run(Graph.values(request=input_request))

    assert source.calls == 0
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_hook_node_rejects_invalid_snapshot_payload_before_loading_plan() -> None:
    loader = PlanLoader()
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="hook config has an unexpected payload type"):
        await _node(InvalidSnapshotPayloadSource(), loader, runtime).run(Graph.values(request=_request()))

    assert loader.snapshots == []
    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_rank", [1, 2, 3])
async def test_hook_node_rejects_invalid_priority_config_before_invocation(invalid_rank: int) -> None:
    source = ConfigSource(_config())
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match=f"hook P{invalid_rank} config has an unexpected payload type"):
        await _node(source, InvalidPriorityConfigLoader(invalid_rank), runtime).run(Graph.values(request=_request()))

    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_rank", [1, 2, 3])
async def test_internal_port_rejects_invalid_invocation_results(invalid_rank: int) -> None:
    runtime = InvalidResultRuntime(invalid_rank)
    node = _node(ConfigSource(_config()), PlanLoader(), runtime)

    with pytest.raises(HookContractError, match="HookStageResult"):
        await node.run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, invalid_rank + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_rank", [1, 2, 3])
async def test_internal_port_rejects_invalid_stage_value_before_next_priority(invalid_rank: int) -> None:
    runtime = InvalidStageValueRuntime(invalid_rank)

    with pytest.raises(HookContractError, match="hook value has an unexpected payload type"):
        await _node(ConfigSource(_config()), PlanLoader(), runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, invalid_rank + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_rank", [1, 2, 3])
async def test_internal_port_rejects_invalid_stage_command_before_next_priority(invalid_rank: int) -> None:
    runtime = InvalidStageCommandRuntime(invalid_rank)

    with pytest.raises(HookContractError, match="hook command has an unexpected payload type"):
        await _node(ConfigSource(_config()), PlanLoader(), runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, invalid_rank + 1))


@pytest.mark.asyncio
async def test_internal_port_rejects_final_result_from_invocation() -> None:
    runtime = FinalResultRuntime()

    with pytest.raises(HookContractError, match="HookStageResult"):
        await _node(ConfigSource(_config()), PlanLoader(), runtime).run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == (1,)


@pytest.mark.asyncio
async def test_hook_node_composes_as_a_nested_graph() -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = SerialRuntime()
    hook = _node(source, loader, runtime)
    parent = Graph[HookGraphValue]("react.parent")
    request_type = cast(type[HookGraphValue], HookRequest)
    parent.add_node(
        "hook",
        hook,
        inputs={"request": Graph.graph_input("request", request_type)},
    )
    parent.set_outputs({"result": Graph.node_output("hook", "result")})

    completion = _completion(await parent.run(Graph.values(request=_request())))

    assert completion.value == "x1a2b3c"
    with pytest.raises(GraphValidationError, match="immutable"):
        hook.set_outputs({"result": Graph.node_output("p3", "result")})


@pytest.mark.asyncio
async def test_parent_can_embed_hooks_whose_legacy_ids_would_collide() -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = SerialRuntime()
    left = HookNode(
        HookSlotId(GraphDefinitionId("a"), GraphDefinitionVersion(1), GraphNodeId("b.hook.c")),
        source,
        loader,
        runtime,
        _admission(),
    )
    right = HookNode(
        HookSlotId(GraphDefinitionId("a.hook.b"), GraphDefinitionVersion(1), GraphNodeId("c")),
        source,
        loader,
        runtime,
        _admission(),
    )
    parent = Graph[HookGraphValue]("collision.parent")
    request_type = cast(type[HookGraphValue], HookRequest)
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
    assert result.outputs["left"] == HookResult("x1a2b3c", (Increment(1), Increment(2), Increment(3)))
    assert result.outputs["right"] == HookResult("x1a2b3c", (Increment(1), Increment(2), Increment(3)))


def test_hook_node_rejects_missing_required_assembly_capabilities() -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="HookSlotId"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            cast(HookSlotId, object()), source, loader, runtime, _admission()
        )
    with pytest.raises(HookContractError, match="config source"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](_slot(), None, loader, runtime, _admission())
    with pytest.raises(HookContractError, match="plan loader"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](_slot(), source, None, runtime, _admission())
    with pytest.raises(HookContractError, match="invocation capability"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](_slot(), source, loader, None, _admission())


def test_hook_node_rejects_missing_and_non_callable_capability_members() -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="config source"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            _slot(),
            cast(HookConfigSource[Config], object()),
            loader,
            runtime,
            _admission(),
        )
    with pytest.raises(HookContractError, match="config source"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            _slot(),
            cast(HookConfigSource[Config], NonCallableSnapshotSource()),
            loader,
            runtime,
            _admission(),
        )
    with pytest.raises(HookContractError, match="plan loader"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            _slot(),
            source,
            cast(HookPlanLoader[Config, PriorityConfig], object()),
            runtime,
            _admission(),
        )
    with pytest.raises(HookContractError, match="plan loader"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            _slot(),
            source,
            cast(HookPlanLoader[Config, PriorityConfig], NonCallablePlanLoader()),
            runtime,
            _admission(),
        )
    with pytest.raises(HookContractError, match="invocation capability"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            _slot(),
            source,
            loader,
            cast(
                Invocation[
                    HookInvocationRequest[PriorityConfig, str, Counter],
                    HookStageResult[str, Increment],
                ],
                object(),
            ),
            _admission(),
        )
    with pytest.raises(HookContractError, match="invocation capability"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            _slot(),
            source,
            loader,
            cast(
                Invocation[
                    HookInvocationRequest[PriorityConfig, str, Counter],
                    HookStageResult[str, Increment],
                ],
                NonCallableInvocation(),
            ),
            _admission(),
        )


def test_hook_node_rejects_an_invalid_payload_admission() -> None:
    with pytest.raises(HookContractError, match="payload admission contract"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](
            _slot(),
            ConfigSource(_config()),
            PlanLoader(),
            SerialRuntime(),
            cast(HookPayloadAdmission[Config, PriorityConfig, str, Counter, Increment], object()),
        )


def test_plan_and_result_validate_their_minimal_nominal_boundaries() -> None:
    snapshot = HookConfigSnapshot(_config())
    priority = HookPriorityPlan(PriorityConfig(1, ("value",)))
    plan = HookPlan(priority, priority, priority)
    stage = HookStageResult("stage", (Increment(1),))
    result = HookResult("result", (Increment(2),))

    assert not hasattr(stage, "state")
    assert not hasattr(result, "state")

    with pytest.raises(FrozenInstanceError):
        snapshot.config = _config("replacement-")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        priority.config = PriorityConfig(1, ())  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.p1 = priority  # type: ignore[misc]
    with pytest.raises(TypeError, match="HookPriorityPlan"):
        HookPlan(cast(HookPriorityPlan[PriorityConfig], object()), priority, priority)
    with pytest.raises(TypeError, match="HookRequest"):
        HookInvocationRequest(
            PriorityConfig(1, ()),
            cast(HookRequest[str, Counter], object()),
        )
    with pytest.raises(FrozenInstanceError):
        stage.value = "replacement"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.value = "replacement"  # type: ignore[misc]
    with pytest.raises(TypeError, match="tuple"):
        HookStageResult("value", cast(tuple[Increment, ...], []))
    with pytest.raises(TypeError, match="tuple"):
        HookResult("value", cast(tuple[Increment, ...], []))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("config", cast(type[Config], object())),
        ("priority config", cast(type[PriorityConfig], object())),
        ("value", cast(type[str], object())),
        ("state", cast(type[Counter], object())),
        ("command", cast(type[Increment], object())),
    ],
)
def test_payload_admission_rejects_erased_descriptor_types(field: str, replacement: type[object]) -> None:
    types: list[type[object]] = [Config, PriorityConfig, str, Counter, Increment]
    index = ("config", "priority config", "value", "state", "command").index(field)
    types[index] = replacement

    with pytest.raises(HookContractError, match=f"hook {field} type must be one concrete nominal class"):
        HookPayloadAdmission(*types)  # type: ignore[arg-type]


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

    with pytest.raises(HookContractError, match="HookRequest"):
        admission.admit_request(cast(HookRequest[str, Counter], object()))
    with pytest.raises(HookContractError, match="HookInvocationRequest"):
        admission.admit_invocation_request(cast(HookInvocationRequest[PriorityConfig, str, Counter], object()))
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
    object.__setattr__(malformed_plan, "p3", priority)

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
    node = _node(ConfigSource(_config()), PlanLoader(), SerialRuntime())
    original = node.slot

    with pytest.raises(AttributeError):
        node.slot = _slot("replacement")  # type: ignore[misc]

    assert node.slot is original

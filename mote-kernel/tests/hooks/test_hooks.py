"""Deterministic tests for the minimal graph-facing HookNode."""

import asyncio
import importlib
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from typing import Protocol, cast

import pytest

import mote_kernel.hooks as hooks_package
import mote_kernel.hooks.contract as hooks_contract
from mote_kernel.execution import Graph
from mote_kernel.hooks import HookNode
from mote_kernel.hooks.contract import (
    HookContractError,
    HookGraphValue,
    HookRequest,
    HookResult,
)
from mote_kernel.hooks.identity import HookSlotId, HookStage
from mote_kernel.hooks.plan import HookConfigSnapshot, HookPlan, HookPriorityPlan
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


class SnapshotSource(Protocol):
    def snapshot(self) -> HookConfigSnapshot[Config]: ...


class DynamicPlanLoader(Protocol):
    def load(self, snapshot: HookConfigSnapshot[Config], /) -> HookPlan[PriorityConfig]: ...


class RuntimeInvocation(Protocol):
    async def invoke(
        self,
        config: PriorityConfig,
        request: HookRequest[str, Counter],
        /,
    ) -> HookResult[str, Increment]: ...


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


class SerialRuntime:
    def __init__(self, after_first: Callable[[], None] | None = None) -> None:
        self.calls: list[InvocationCall] = []
        self.after_first = after_first

    async def invoke(
        self,
        config: PriorityConfig,
        request: HookRequest[str, Counter],
        /,
    ) -> HookResult[str, Increment]:
        self.calls.append(InvocationCall(config, request))
        value = request.value
        for fragment in config.fragments:
            value = f"{value}{fragment}"
        if config.rank == 1 and self.after_first is not None:
            self.after_first()
        commands = (Increment(config.rank),) if config.fragments else ()
        return HookResult(value, commands)


class ParallelRuntime:
    def __init__(self) -> None:
        self.calls: list[InvocationCall] = []

    @staticmethod
    async def _fragment(fragment: str) -> str:
        await asyncio.sleep(0)
        return fragment

    async def invoke(
        self,
        config: PriorityConfig,
        request: HookRequest[str, Counter],
        /,
    ) -> HookResult[str, Increment]:
        self.calls.append(InvocationCall(config, request))
        fragments = await asyncio.gather(*(self._fragment(fragment) for fragment in config.fragments))
        commands = (Increment(config.rank),) if fragments else ()
        return HookResult(f"{request.value}{''.join(fragments)}", commands)


class FailingRuntime:
    def __init__(self, failure_rank: int) -> None:
        self.failure_rank = failure_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        config: PriorityConfig,
        request: HookRequest[str, Counter],
        /,
    ) -> HookResult[str, Increment]:
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.failure_rank:
            raise RuntimeError("invocation failed")
        return HookResult(f"{request.value}{''.join(config.fragments)}")


class InvalidResultRuntime:
    def __init__(self, invalid_rank: int) -> None:
        self.invalid_rank = invalid_rank
        self.calls: list[InvocationCall] = []

    async def invoke(
        self,
        config: PriorityConfig,
        request: HookRequest[str, Counter],
        /,
    ) -> HookResult[str, Increment]:
        self.calls.append(InvocationCall(config, request))
        if config.rank == self.invalid_rank:
            return cast(HookResult[str, Increment], object())
        return HookResult(f"{request.value}{''.join(config.fragments)}")


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
    source: SnapshotSource,
    loader: DynamicPlanLoader,
    invocation: RuntimeInvocation,
) -> HookNode[Config, PriorityConfig, str, Counter, Increment]:
    return HookNode(_slot(), source, loader, invocation)


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
@pytest.mark.parametrize("invalid_rank", [1, 2, 3])
async def test_internal_port_rejects_invalid_invocation_results(invalid_rank: int) -> None:
    runtime = InvalidResultRuntime(invalid_rank)
    node = _node(ConfigSource(_config()), PlanLoader(), runtime)

    with pytest.raises(HookContractError, match="HookResult"):
        await node.run(Graph.values(request=_request()))

    assert tuple(call.config.rank for call in runtime.calls) == tuple(range(1, invalid_rank + 1))


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


def test_hook_node_rejects_missing_required_assembly_capabilities() -> None:
    source = ConfigSource(_config())
    loader = PlanLoader()
    runtime = SerialRuntime()

    with pytest.raises(HookContractError, match="HookSlotId"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](cast(HookSlotId, object()), source, loader, runtime)
    with pytest.raises(HookContractError, match="config source"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](_slot(), None, loader, runtime)
    with pytest.raises(HookContractError, match="plan loader"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](_slot(), source, None, runtime)
    with pytest.raises(HookContractError, match="invocation capability"):
        HookNode[Config, PriorityConfig, str, Counter, Increment](_slot(), source, loader, None)


def test_plan_and_result_validate_their_minimal_nominal_boundaries() -> None:
    snapshot = HookConfigSnapshot(_config())
    priority = HookPriorityPlan(PriorityConfig(1, ("value",)))
    plan = HookPlan(priority, priority, priority)

    with pytest.raises(FrozenInstanceError):
        snapshot.config = _config("replacement-")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        priority.config = PriorityConfig(1, ())  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.p1 = priority  # type: ignore[misc]
    with pytest.raises(TypeError, match="HookPriorityPlan"):
        HookPlan(cast(HookPriorityPlan[PriorityConfig], object()), priority, priority)
    with pytest.raises(TypeError, match="tuple"):
        HookResult("value", cast(tuple[Increment, ...], []))


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


def test_hook_node_is_the_only_package_level_api_and_external_port_spi_is_removed() -> None:
    assert hooks_package.__all__ == ["HookNode"]
    assert "HookPort" not in hooks_contract.__all__
    assert not hasattr(hooks_contract, "HookPort")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("mote_kernel.hooks.manager")

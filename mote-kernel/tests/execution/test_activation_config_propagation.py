import pytest
from tests.execution.engine.factories import activation_config

from mote_kernel.config import Config
from mote_kernel.execution import Graph


def _recording_node(seen: list[Config | None]):
    async def operation(values: Graph.Values[str]) -> Graph.Values[str]:
        seen.append(values.activation_config)
        return Graph.values()

    return operation


@pytest.mark.asyncio
async def test_start_node_without_business_inputs_inherits_activation_config() -> None:
    seen: list[Config | None] = []
    graph = Graph[str]("config-propagation.start")
    graph.add_node("start", _recording_node(seen), inputs={}, outputs={})
    graph.add_edge("start", Graph.END)
    graph.set_outputs({})
    config = activation_config(1)

    result = await graph.run(Graph.values(), activation_config=config)

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [config]
    assert seen[0] is config


@pytest.mark.asyncio
async def test_direct_routed_node_without_business_inputs_inherits_cause_config() -> None:
    seen: list[Config | None] = []
    graph = Graph[str]("config-propagation.direct")
    graph.add_node("source", _recording_node(seen), inputs={}, outputs={})
    graph.add_node("target", _recording_node(seen), inputs={}, outputs={})
    graph.add_edge("source", "target")
    graph.add_edge("target", Graph.END)
    graph.set_outputs({})
    config = activation_config(1)

    result = await graph.run(Graph.values(), activation_config=config)

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [config, config]
    assert all(candidate is config for candidate in seen)


@pytest.mark.asyncio
async def test_join_node_without_business_inputs_merges_all_cause_configs() -> None:
    seen: list[Config | None] = []
    graph = Graph[str]("config-propagation.join")
    graph.add_node("left", _recording_node(seen), inputs={}, outputs={})
    graph.add_node("right", _recording_node(seen), inputs={}, outputs={})
    graph.add_node("joined", _recording_node(seen), inputs={}, outputs={})
    graph.add_join(("left", "right"), "joined")
    graph.add_edge("joined", Graph.END)
    graph.set_outputs({})
    config = activation_config(1)

    result = await graph.run(Graph.values(), activation_config=config)

    assert isinstance(result, Graph.CompletedResult)
    assert seen == [config, config, config]
    assert all(candidate is config for candidate in seen)

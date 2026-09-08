from enum import StrEnum

import pytest

from mote_kernel.execution import Graph
from mote_kernel.state.graph_state import GraphNodeId, SettleGraphNode


class _NodeId(StrEnum):
    PROJECT = "project"


class _RouteId(StrEnum):
    COMPLETE = "complete"


class _ValueName(StrEnum):
    REQUEST = "request"
    RESULT = "result"


@pytest.mark.asyncio
async def test_graph_canonicalizes_enum_node_route_and_value_names() -> None:
    settled_node_ids: list[GraphNodeId] = []

    async def project(values: Graph.Values[str], /) -> Graph.Outcome[str]:
        assert values[_ValueName.REQUEST] == "input"
        return Graph.success(
            Graph.values(**{_ValueName.RESULT: "output"}),
            route=_RouteId.COMPLETE,
        )

    async def commit(transition: Graph.Transition[str], /) -> Graph.State:
        if isinstance(transition.command, SettleGraphNode):
            settled_node_ids.append(transition.command.outcome.node_id)
        return transition.candidate_state

    graph = Graph[str]("enum-identifiers")
    request = Graph.graph_input(_ValueName.REQUEST, str)
    graph.add_node(
        _NodeId.PROJECT,
        project,
        inputs={_ValueName.REQUEST: request},
        outputs={_ValueName.RESULT: str},
    )
    graph.add_edge(Graph.START, _NodeId.PROJECT)
    graph.add_edge(_NodeId.PROJECT, _RouteId.COMPLETE, Graph.END)
    graph.set_outputs(
        {
            _ValueName.RESULT: graph.node_output(
                _NodeId.PROJECT,
                _ValueName.RESULT,
            )
        }
    )

    result = await graph.run(Graph.values(request="input"), commit=commit)

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs[_ValueName.RESULT] == "output"
    assert result.outputs.keys() == ("result",)
    assert type(result.outputs.keys()[0]) is str
    assert settled_node_ids == ["project"]
    assert type(settled_node_ids[0]) is str
    assert result.state.completion_route == "complete"
    assert type(result.state.completion_route) is str

import asyncio

import pytest
from tests.execution.persistence_fixtures import STRING_CODEC, MemoryPersistence

from mote_kernel.execution import Graph
from mote_kernel.execution.persistence import DurableGraphCommit, GraphPersistenceCommit, GraphRecovery


@pytest.mark.asyncio
async def test_partial_sibling_settlement_recovers_join_from_confirmed_evidence_only() -> None:
    calls: list[str] = []
    left_committed = asyncio.Event()

    def graph(gate: asyncio.Event | None = None) -> Graph[str]:
        result = Graph[str]("persistent-join")

        async def left(values: Graph.Values[str]) -> Graph.Values[str]:
            calls.append("left")
            return Graph.values(value=values["value"] + "-left")

        async def right(values: Graph.Values[str]) -> Graph.Values[str]:
            if gate is not None:
                await gate.wait()
            calls.append("right")
            return Graph.values(value=values["value"] + "-right")

        async def join(values: Graph.Values[str]) -> Graph.Values[str]:
            calls.append("join")
            return Graph.values(value=values["left"] + "|" + values["right"])

        result.add_node("left", left, inputs={"value": result.graph_input("value", str)}, outputs={"value": str})
        result.add_node("right", right, inputs={"value": result.graph_input("value", str)}, outputs={"value": str})
        result.add_node(
            "join",
            join,
            inputs={"left": result.node_output("left", "value"), "right": result.node_output("right", "value")},
            outputs={"value": str},
        )
        result.add_edge(Graph.START, "left")
        result.add_edge(Graph.START, "right")
        result.add_join(("left", "right"), "join")
        result.add_edge("join", Graph.END)
        result.set_outputs({"value": result.output_ref("join", "value")})
        return result

    store = MemoryPersistence[str]()
    store.fail_when = lambda request: any(
        item.coordinate.activation.node_id == "right" for item in request.writes.publications
    )

    async def commit(request: GraphPersistenceCommit[str], /) -> GraphPersistenceCommit[str]:
        confirmed = await store(request)
        if any(item.coordinate.activation.node_id == "left" for item in request.writes.publications):
            left_committed.set()
        return confirmed

    with pytest.raises(OSError, match="injected persistence outage"):
        await asyncio.wait_for(
            graph(left_committed).run(
                Graph.values(value="input"), run_id="run", commit=DurableGraphCommit(STRING_CODEC, commit)
            ),
            timeout=2,
        )
    checkpoint = store.checkpoint()
    assert calls == ["left", "right"]
    assert [record.coordinate.activation.node_id for record in checkpoint.publications] == ["left"]
    store.reopen()
    result = await graph().run(recovery=GraphRecovery(checkpoint, DurableGraphCommit(STRING_CODEC, store)))
    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["value"] == "input-left|input-right"
    assert calls == ["left", "right", "right", "join"]

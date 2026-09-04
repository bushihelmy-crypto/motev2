from mote_kernel.execution import Graph


async def increment(values: Graph.Values[int]) -> Graph.Outcome[int]:
    return Graph.success(Graph.values(value=values["value"] + 1), route="done")


graph = Graph[int]("typing.feedback-cross-universe")
graph.add_node(
    "loop",
    increment,
    inputs={
        "value": Graph.feedback(
            initial=Graph.graph_input("seed", str),
            repeat=Graph.node_output("loop", "value"),
        )
    },
    outputs={"value": int},
)

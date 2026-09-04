from mote_kernel.execution import Graph


async def complete(values: Graph.Values[int]) -> Graph.Values[int]:
    return values


child = Graph[int]("typing.feedback-child")
child.add_node(
    "leaf",
    complete,
    inputs={"value": Graph.graph_input("value", int)},
    outputs={"value": int},
)
child.set_outputs({"value": Graph.node_output("leaf", "value")})

parent = Graph[int]("typing.feedback-parent")
parent.add_node(
    "child",
    child,
    inputs={
        "value": Graph.feedback(
            initial=Graph.graph_input("seed", int),
            repeat=Graph.node_output("child", "value"),
        )
    },
)

"""Strict typing examples for fixed and causal node-output references."""

from mote_kernel.execution import Graph


async def passthrough(values: Graph.Values[int]) -> Graph.Values[int]:
    return values


graph = Graph[int]("typing.node-output-overloads")
graph.add_node(
    "initialize",
    passthrough,
    inputs={"value": Graph.graph_input("seed", int)},
    outputs={"value": int},
)
graph.add_node(
    "causal-consumer",
    passthrough,
    inputs={"value": Graph.node_output("value")},
    outputs={"value": int},
)
graph.add_node(
    "fixed-consumer",
    passthrough,
    inputs={"value": Graph.node_output("initialize", "value")},
    outputs={"value": int},
)

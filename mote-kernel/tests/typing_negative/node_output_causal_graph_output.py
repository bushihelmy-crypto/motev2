from mote_kernel.execution import Graph

graph = Graph[int]("typing.causal-graph-output")
graph.set_outputs({"value": Graph.node_output("value")})

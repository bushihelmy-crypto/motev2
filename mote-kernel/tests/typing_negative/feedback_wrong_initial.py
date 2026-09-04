from mote_kernel.execution import Graph

Graph.feedback(
    initial=42,
    repeat=Graph.node_output("loop", "value"),
)

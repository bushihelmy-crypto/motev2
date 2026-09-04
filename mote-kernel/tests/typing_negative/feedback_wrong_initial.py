from mote_kernel.execution import Graph

Graph.feedback(
    initial=Graph.node_output("previous", "value"),
    repeat=Graph.node_output("loop", "value"),
)

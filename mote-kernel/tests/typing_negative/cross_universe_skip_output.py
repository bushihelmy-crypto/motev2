from mote_kernel.execution import Graph


class UniverseA:
    pass


class UniverseB:
    pass


graph_a = Graph[UniverseA]("typing.skip-output.a")
action_a = graph_a.skip_failed(
    "node",
    "replacement",
    output=Graph.values(value=UniverseA()),
)

graph_b = Graph[UniverseB]("typing.skip-output.b")
action_b: Graph.ResumeAction[UniverseB] = action_a

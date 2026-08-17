from typing import Never, assert_type

from mote_kernel.execution import Graph

graph = Graph[str | int]("typing.skip-output.factory")
heterogeneous = graph.skip_failed(
    "node",
    "replacement",
    output=Graph.values(text="value", number=1),
)
empty = graph.skip_failed("node", "replacement", output=Graph.values())
never_graph = Graph[Never]("typing.skip-output.never")
never_action = never_graph.skip_failed("node", "replacement", output=Graph.values())

assert_type(heterogeneous, Graph.ResumeAction[str | int])
assert_type(empty, Graph.ResumeAction[str | int])
assert_type(never_action, Graph.ResumeAction[Never])

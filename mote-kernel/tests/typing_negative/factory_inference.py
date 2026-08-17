from typing import Never, assert_type

from mote_kernel.execution import Graph

empty = Graph.values()
heterogeneous = Graph.values(text="value", number=1)

assert_type(empty, Graph.Values[Never])
assert_type(heterogeneous, Graph.Values[str | int])

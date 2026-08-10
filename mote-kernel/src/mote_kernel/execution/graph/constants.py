"""Virtual graph boundary identities."""

from mote_kernel.execution.graph.identity import NodeId

END = NodeId("__end__")
"""Virtual target that terminates a graph path without scheduling a task."""

__all__ = ["END"]

"""Virtual graph boundary identities."""

from mote_kernel.state.graph_state.identity import GraphNodeId

START = GraphNodeId("__start__")
"""Virtual source that enters a graph path without scheduling a task."""

END = GraphNodeId("__end__")
"""Virtual target that terminates a graph path without scheduling a task."""

__all__ = ["END", "START"]

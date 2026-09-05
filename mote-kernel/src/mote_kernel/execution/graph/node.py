"""Canonical callable graph-node definitions."""

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.execution.graph.outcome import GraphOutcome
from mote_kernel.execution.graph.ports import InputBindings, OutputDeclarations
from mote_kernel.execution.graph.values import _GraphValues
from mote_kernel.execution.resource import ResourceId
from mote_kernel.state.graph_state import GraphNodeId

GraphValueT = TypeVar("GraphValueT")


class NodeCallable(Protocol[GraphValueT]):
    """Kernel-owned program for one graph node.

    This is the graph execution contract, not the Runtime ``Invocation``
    capability used behind domain Ports.  The scheduler is the only owner
    that invokes it.
    """

    def __call__(
        self,
        values: _GraphValues[GraphValueT],
        /,
    ) -> Awaitable[_GraphValues[GraphValueT] | GraphOutcome[GraphValueT]]: ...


@dataclass(frozen=True, slots=True)
class CallableNodeDefinition(Generic[GraphValueT]):
    """Immutable topology entry for one already-assembled Kernel node."""

    node_id: GraphNodeId
    operation: NodeCallable[GraphValueT]
    inputs: InputBindings[GraphValueT]
    outputs: OutputDeclarations[GraphValueT]
    resources: tuple[ResourceId, ...] = ()


__all__: list[str] = []

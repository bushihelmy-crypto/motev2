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
    """A graph node returns an awaitable that the execution engine owns."""

    def __call__(
        self,
        values: _GraphValues[GraphValueT],
        /,
    ) -> Awaitable[_GraphValues[GraphValueT] | GraphOutcome[GraphValueT]]: ...


@dataclass(frozen=True, slots=True)
class CallableNodeDefinition(Generic[GraphValueT]):
    node_id: GraphNodeId
    operation: NodeCallable[GraphValueT]
    inputs: InputBindings[GraphValueT]
    outputs: OutputDeclarations[GraphValueT]
    resources: tuple[ResourceId, ...] = ()


__all__: list[str] = []

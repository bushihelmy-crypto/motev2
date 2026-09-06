"""Canonical callable graph-node definitions."""

from collections.abc import Awaitable
from dataclasses import InitVar, dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.execution.errors import GraphValueAdmissionError
from mote_kernel.execution.graph.outcome import (
    GraphOutcome,
    _GraphFailureOutcome,
    _GraphInterruptOutcome,
    _GraphSuccessOutcome,
)
from mote_kernel.execution.graph.ports import (
    InputBindings,
    OutputDeclarations,
    TypedInputBinding,
)
from mote_kernel.execution.graph.values import (
    NodeInputFrame,
    _frame_value_typed,
    _GraphValues,
)
from mote_kernel.execution.resource import ResourceId
from mote_kernel.state.graph_state import GraphNodeId

GraphValueT = TypeVar("GraphValueT")
InputT_contra = TypeVar("InputT_contra", contravariant=True)
MaterializedT_co = TypeVar("MaterializedT_co", covariant=True)
OutputT_co = TypeVar("OutputT_co", covariant=True)
SlotValueT = TypeVar("SlotValueT")


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


class NodeOperation(Protocol[InputT_contra, OutputT_co]):
    """One domain operation behind the Graph-owned typed adapter."""

    def __call__(
        self,
        value: InputT_contra,
        /,
    ) -> Awaitable[OutputT_co | _GraphSuccessOutcome[OutputT_co] | _GraphFailureOutcome | _GraphInterruptOutcome]: ...


class NodeInvoker(Protocol[GraphValueT]):
    """The sole scheduler-facing callable-node execution contract."""

    def __call__(
        self,
        frame: NodeInputFrame[GraphValueT],
        /,
    ) -> Awaitable[_GraphValues[GraphValueT] | GraphOutcome[GraphValueT]]: ...


class _NodeInputsSeal:
    __slots__ = ()


_NODE_INPUTS_SEAL = _NodeInputsSeal()


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeInputs(Generic[GraphValueT]):
    """Read-only typed view used only while materializing one node DTO."""

    _frame: NodeInputFrame[GraphValueT]
    _bindings: tuple[TypedInputBinding[GraphValueT], ...]
    _seal: InitVar[_NodeInputsSeal]

    def __post_init__(self, _seal: _NodeInputsSeal) -> None:
        if _seal is not _NODE_INPUTS_SEAL:
            raise GraphValueAdmissionError("typed node inputs require their execution owner")

    def get(self, binding: TypedInputBinding[SlotValueT], /) -> SlotValueT:
        if type(binding) is not TypedInputBinding or not any(candidate is binding for candidate in self._bindings):
            raise GraphValueAdmissionError("typed node materializer requested an undeclared input slot")
        descriptor = binding.source.descriptor
        if descriptor is None:
            raise GraphValueAdmissionError("admitted typed node input lacks its nominal descriptor")
        return _frame_value_typed(
            self._frame,
            binding.name,
            descriptor,
        )


def _make_node_inputs(
    frame: NodeInputFrame[GraphValueT],
    bindings: tuple[TypedInputBinding[GraphValueT], ...],
) -> NodeInputs[GraphValueT]:
    return NodeInputs(_frame=frame, _bindings=bindings, _seal=_NODE_INPUTS_SEAL)


class NodeInputMaterializer(Protocol[GraphValueT, MaterializedT_co]):
    """Assemble one immutable operation DTO from declared typed slots."""

    def __call__(self, values: NodeInputs[GraphValueT], /) -> MaterializedT_co: ...


@dataclass(frozen=True, slots=True)
class CallableNodeDefinition(Generic[GraphValueT]):
    """Immutable topology entry for one already-assembled Kernel node."""

    node_id: GraphNodeId
    invoker: NodeInvoker[GraphValueT]
    inputs: InputBindings[GraphValueT]
    outputs: OutputDeclarations[GraphValueT]
    resources: tuple[ResourceId, ...] = ()


__all__ = [
    "CallableNodeDefinition",
    "NodeCallable",
    "NodeInputMaterializer",
    "NodeInputs",
    "NodeInvoker",
    "NodeOperation",
    "_make_node_inputs",
]

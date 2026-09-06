"""Canonical callable graph-node definitions."""

from collections.abc import Awaitable
from dataclasses import InitVar, dataclass
from typing import Generic, Protocol, TypeVar, cast

from mote_kernel.execution.errors import GraphValidationError, GraphValueAdmissionError
from mote_kernel.execution.graph.outcome import (
    GraphOutcome,
    _GraphFailureOutcome,
    _GraphInterruptOutcome,
    _GraphSuccessOutcome,
)
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    InputBinding,
    InputBindings,
    NodeInputSlot,
    NodeOutputRef,
    NodeOutputSlot,
    NominalTypeDescriptor,
    OutputDeclaration,
    OutputDeclarations,
    PredecessorOutputRef,
    TypedInputBinding,
    canonical_nominal_type,
    canonical_port_name,
)
from mote_kernel.execution.graph.values import (
    NodeInputFrame,
    _frame_value_typed,
    _GraphValues,
    _make_single_graph_value,
    admit_exact,
)
from mote_kernel.execution.resource import ResourceId
from mote_kernel.state.graph_state import GraphNodeId

GraphValueT = TypeVar("GraphValueT")
InputT = TypeVar("InputT")
InputT_contra = TypeVar("InputT_contra", contravariant=True)
MaterializedT = TypeVar("MaterializedT")
MaterializedT_co = TypeVar("MaterializedT_co", covariant=True)
PublisherValueT = TypeVar("PublisherValueT")
OutputT = TypeVar("OutputT")
OutputT_co = TypeVar("OutputT_co", covariant=True)
SlotValueT = TypeVar("SlotValueT")


def _validate_typed_binding(binding: TypedInputBinding[GraphValueT]) -> None:
    """Validate one facade binding before any field is used for ordering."""

    destination = binding.destination
    if type(destination) is not NodeInputSlot:
        raise GraphValidationError("typed node contract contains a malformed input slot")
    canonical_port_name(destination.name, kind="input")
    if type(destination.descriptor) is not NominalTypeDescriptor:
        raise GraphValidationError("typed node contract input slot has a malformed descriptor")
    try:
        canonical_nominal_type(destination.descriptor.value_type)
    except GraphValidationError as error:
        raise GraphValidationError("typed node contract input slot has a non-concrete descriptor") from error
    source = binding.source
    if type(source) not in (GraphInputRef, NodeOutputRef, PredecessorOutputRef):
        raise GraphValidationError("typed node contract contains a malformed input source")
    source_descriptor = source.descriptor
    if source_descriptor is None or source_descriptor is not destination.descriptor:
        raise GraphValidationError("typed node contract input binding has a descriptor mismatch")
    try:
        canonical_nominal_type(source_descriptor.value_type)
    except GraphValidationError as error:
        raise GraphValidationError("typed node contract input source has a non-concrete descriptor") from error


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
        return _frame_value_typed(
            self._frame,
            binding.destination.name,
            binding.destination.descriptor,
        )


def _make_node_inputs(
    frame: NodeInputFrame[GraphValueT],
    bindings: tuple[TypedInputBinding[GraphValueT], ...],
) -> NodeInputs[GraphValueT]:
    return NodeInputs(_frame=frame, _bindings=bindings, _seal=_NODE_INPUTS_SEAL)


class NodeInputMaterializer(Protocol[GraphValueT, MaterializedT_co]):
    """Assemble one immutable operation DTO from declared typed slots."""

    def __call__(self, values: NodeInputs[GraphValueT], /) -> MaterializedT_co: ...


class NodeOutputPublisher(Protocol[PublisherValueT]):
    """Publish one admitted DTO through the canonical graph-value owner."""

    def __call__(self, value: PublisherValueT, /) -> _GraphValues[PublisherValueT]: ...


@dataclass(frozen=True, slots=True)
class NodeContract(Generic[GraphValueT, InputT, OutputT]):
    """Immutable typed declaration consumed by the sole execution adapter."""

    bindings: tuple[TypedInputBinding[GraphValueT], ...]
    input_descriptor: NominalTypeDescriptor[InputT]
    output: NodeOutputSlot[OutputT]
    operation: NodeOperation[InputT, OutputT]
    input_materializer: NodeInputMaterializer[GraphValueT, InputT]
    output_publisher: NodeOutputPublisher[OutputT]

    @property
    def output_descriptor(self) -> NominalTypeDescriptor[OutputT]:
        """Return the output descriptor owned by this contract's slot."""

        return self.output.descriptor

    def __post_init__(self) -> None:
        if type(self.bindings) is not tuple:
            raise GraphValidationError("typed node contract contains malformed input bindings")
        for binding in self.bindings:
            if type(binding) is not TypedInputBinding:
                raise GraphValidationError("typed node contract contains malformed input bindings")
            _validate_typed_binding(binding)
        names = tuple(binding.destination.name for binding in self.bindings)
        if names != tuple(sorted(set(names))):
            raise GraphValidationError("typed node contract input bindings must be canonical and unique")
        if type(self.input_descriptor) is not NominalTypeDescriptor:
            raise GraphValidationError("typed node contract requires one concrete input descriptor")
        try:
            canonical_nominal_type(self.input_descriptor.value_type)
        except GraphValidationError as error:
            raise GraphValidationError("typed node contract requires one concrete input descriptor") from error
        if type(self.output) is not NodeOutputSlot:
            raise GraphValidationError("typed node contract requires one output slot")
        if type(self.output.descriptor) is not NominalTypeDescriptor:
            raise GraphValidationError("typed node contract requires one concrete output descriptor")
        try:
            canonical_nominal_type(self.output.descriptor.value_type)
        except GraphValidationError as error:
            raise GraphValidationError("typed node contract requires one concrete output descriptor") from error
        canonical_port_name(self.output.name, kind="output")
        if not callable(self.operation) or not callable(self.input_materializer) or not callable(self.output_publisher):
            raise GraphValidationError("typed node contract requires callable materializer, publisher, and operation")


@dataclass(frozen=True, slots=True)
class TypedNodeAssembly(Generic[GraphValueT, InputT, OutputT]):
    """The atomic lowering of one typed contract into graph definition IR."""

    contract: NodeContract[GraphValueT, InputT, OutputT]
    inputs: InputBindings[GraphValueT]
    outputs: OutputDeclarations[GraphValueT]
    output_ref: NodeOutputRef[OutputT]


def make_typed_node_assembly(
    node_id: GraphNodeId,
    operation: NodeOperation[InputT, OutputT],
    bindings: tuple[TypedInputBinding[GraphValueT], ...],
    input_type: type[InputT],
    materialize: NodeInputMaterializer[GraphValueT, InputT],
    output_name: str,
    output_type: type[OutputT],
) -> TypedNodeAssembly[GraphValueT, InputT, OutputT]:
    """Create the contract, lowered bindings, and typed output handle together."""

    canonical_port_name(str(node_id), kind="node")
    if type(bindings) is not tuple:
        raise GraphValidationError("typed node inputs must be a tuple of Graph.bind() results")
    for binding in bindings:
        if type(binding) is not TypedInputBinding:
            raise GraphValidationError("typed node inputs must be a tuple of Graph.bind() results")
        _validate_typed_binding(binding)
    ordered = tuple(sorted(bindings, key=lambda binding: binding.destination.name))
    if len(ordered) != len({binding.destination.name for binding in ordered}):
        raise GraphValidationError("typed inputs require unique canonical destination slots")
    input_descriptor = canonical_nominal_type(input_type)
    output_slot = NodeOutputSlot(canonical_port_name(output_name, kind="output"), canonical_nominal_type(output_type))

    def publish(value: OutputT, /) -> _GraphValues[OutputT]:
        admitted = admit_exact(value, output_slot.descriptor, kind=f"typed node output {output_slot.name!r}")
        return _make_single_graph_value(output_slot.name, admitted)

    contract = NodeContract(
        bindings=ordered,
        input_descriptor=input_descriptor,
        output=output_slot,
        operation=operation,
        input_materializer=materialize,
        output_publisher=publish,
    )
    lowered_inputs = InputBindings(
        tuple(
            InputBinding(binding.destination.name, binding.source, binding.destination.descriptor)
            for binding in ordered
        )
    )
    declarations = cast(
        OutputDeclarations[GraphValueT],
        OutputDeclarations((OutputDeclaration(output_slot.name, output_slot.descriptor),)),
    )
    output_ref = NodeOutputRef(node_id, output_slot.name, output_slot.descriptor)
    return TypedNodeAssembly(contract, lowered_inputs, declarations, output_ref)


class TypedNodeInvoker(Protocol[GraphValueT]):
    """Existential execution view hiding one contract's concrete DTO pair."""

    def __call__(
        self,
        frame: NodeInputFrame[GraphValueT],
        /,
    ) -> Awaitable[_GraphValues[GraphValueT] | GraphOutcome[GraphValueT]]: ...


@dataclass(frozen=True, slots=True)
class CallableNodeDefinition(Generic[GraphValueT]):
    """Immutable topology entry for one already-assembled Kernel node."""

    node_id: GraphNodeId
    operation: NodeCallable[GraphValueT] | None
    inputs: InputBindings[GraphValueT]
    outputs: OutputDeclarations[GraphValueT]
    resources: tuple[ResourceId, ...] = ()
    typed_invoker: TypedNodeInvoker[GraphValueT] | None = None

    def __post_init__(self) -> None:
        if (self.operation is None) == (self.typed_invoker is None):
            raise GraphValidationError("callable node requires exactly one execution contract")


__all__ = [
    "CallableNodeDefinition",
    "NodeCallable",
    "NodeContract",
    "NodeInputMaterializer",
    "NodeInputs",
    "NodeOperation",
    "NodeOutputPublisher",
    "TypedNodeAssembly",
    "TypedNodeInvoker",
    "_make_node_inputs",
    "make_typed_node_assembly",
]

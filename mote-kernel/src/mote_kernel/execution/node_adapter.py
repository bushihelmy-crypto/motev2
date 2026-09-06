"""Canonical assembly adapters for both callable-node declaration surfaces."""

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution.errors import GraphValidationError
from mote_kernel.execution.graph.node import (
    CallableNodeDefinition,
    NodeCallable,
    NodeInputMaterializer,
    NodeInvoker,
    NodeOperation,
    _make_node_inputs,
)
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
    NodeOutputRef,
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
    _GraphValues,
    _make_single_graph_value,
    _public_values,
    admit_exact,
)
from mote_kernel.execution.resource import ResourceId
from mote_kernel.state.graph_state import GraphNodeId

GraphValueT = TypeVar("GraphValueT")
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class TypedNodeAssembly(Generic[GraphValueT, OutputT]):
    """One atomic lowering from a typed operation to canonical graph IR."""

    definition: CallableNodeDefinition[GraphValueT]
    output_ref: NodeOutputRef[OutputT]


@dataclass(frozen=True, slots=True)
class _ValuesNodeInvoker(Generic[GraphValueT]):
    operation: NodeCallable[GraphValueT]

    async def __call__(
        self,
        frame: NodeInputFrame[GraphValueT],
        /,
    ) -> _GraphValues[GraphValueT] | GraphOutcome[GraphValueT]:
        return await self.operation(_public_values(frame))


@dataclass(frozen=True, slots=True)
class _TypedNodeInvoker(Generic[GraphValueT, InputT, OutputT]):
    bindings: tuple[TypedInputBinding[GraphValueT], ...]
    input_descriptor: NominalTypeDescriptor[InputT]
    output: OutputDeclaration[OutputT]
    operation: NodeOperation[InputT, OutputT]
    materialize: NodeInputMaterializer[GraphValueT, InputT]

    async def __call__(
        self,
        frame: NodeInputFrame[GraphValueT],
        /,
    ) -> _GraphValues[GraphValueT] | GraphOutcome[GraphValueT]:
        inputs = _make_node_inputs(frame, self.bindings)
        materialized = self.materialize(inputs)
        typed_input = admit_exact(
            materialized,
            self.input_descriptor,
            kind="typed node materialized input",
        )
        result = await self.operation(typed_input)
        if type(result) in (_GraphSuccessOutcome, _GraphFailureOutcome, _GraphInterruptOutcome):
            return cast(GraphOutcome[GraphValueT], result)
        admitted = admit_exact(
            cast(OutputT, result),
            self.output.descriptor,
            kind=f"typed node output {self.output.name!r}",
        )
        return cast(_GraphValues[GraphValueT], _make_single_graph_value(self.output.name, admitted))


def make_node_invoker(operation: NodeCallable[GraphValueT]) -> NodeInvoker[GraphValueT]:
    """Adapt the Graph.Values surface once, before it enters compiled IR."""

    return _ValuesNodeInvoker(operation)


def make_typed_node_assembly(
    node_id: GraphNodeId,
    operation: NodeOperation[InputT, OutputT],
    bindings: tuple[TypedInputBinding[GraphValueT], ...],
    input_type: type[InputT],
    materialize: NodeInputMaterializer[GraphValueT, InputT],
    output_name: str,
    output_type: type[OutputT],
    resources: tuple[ResourceId, ...],
) -> TypedNodeAssembly[GraphValueT, OutputT]:
    """Validate and lower a typed node into the sole callable definition shape."""

    canonical_port_name(str(node_id), kind="node")
    if type(bindings) is not tuple:
        raise GraphValidationError("typed node inputs must be a tuple of Graph.bind() results")
    for binding in bindings:
        if type(binding) is not TypedInputBinding:
            raise GraphValidationError("typed node inputs must be a tuple of Graph.bind() results")
        canonical_port_name(binding.name, kind="input")
        source = binding.source
        if type(source) not in (GraphInputRef, NodeOutputRef, PredecessorOutputRef):
            raise GraphValidationError("typed node contract contains a malformed input source")
        descriptor = source.descriptor
        if type(descriptor) is not NominalTypeDescriptor:
            raise GraphValidationError("typed node input source lacks one concrete descriptor")
        try:
            canonical_nominal_type(descriptor.value_type)
        except GraphValidationError as error:
            raise GraphValidationError("typed node input source has a non-concrete descriptor") from error
    if not callable(materialize):
        raise GraphValidationError("typed node contract requires a callable materializer")
    ordered = tuple(sorted(bindings, key=lambda binding: binding.name))
    if len(ordered) != len({binding.name for binding in ordered}):
        raise GraphValidationError("typed inputs require unique canonical destination names")
    input_descriptor = canonical_nominal_type(input_type)
    output = OutputDeclaration(canonical_port_name(output_name, kind="output"), canonical_nominal_type(output_type))
    invoker = _TypedNodeInvoker(
        ordered,
        input_descriptor,
        output,
        operation,
        materialize,
    )
    lowered_inputs = InputBindings(tuple(InputBinding(binding.name, binding.source) for binding in ordered))
    declarations = cast(OutputDeclarations[GraphValueT], OutputDeclarations((output,)))
    definition = CallableNodeDefinition(node_id, invoker, lowered_inputs, declarations, resources)
    return TypedNodeAssembly(definition, NodeOutputRef(node_id, output.name, output.descriptor))


__all__ = ["TypedNodeAssembly", "make_node_invoker", "make_typed_node_assembly"]

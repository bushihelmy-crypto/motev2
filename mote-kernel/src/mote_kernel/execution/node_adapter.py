"""The sole callable-node adaptation boundary used by the scheduler."""

from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.execution.errors import GraphValueAdmissionError
from mote_kernel.execution.graph.node import (
    CallableNodeDefinition,
    NodeCallable,
    NodeContract,
    TypedNodeInvoker,
    _make_node_inputs,
)
from mote_kernel.execution.graph.outcome import (
    GraphOutcome,
    _GraphFailureOutcome,
    _GraphInterruptOutcome,
    _GraphSuccessOutcome,
)
from mote_kernel.execution.graph.values import (
    NodeInputFrame,
    _GraphValues,
    _public_node_input,
    admit_exact,
)

GraphValueT = TypeVar("GraphValueT")
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class _TypedNodeAdapter(Generic[GraphValueT, InputT, OutputT]):
    contract: NodeContract[GraphValueT, InputT, OutputT]

    async def __call__(
        self,
        frame: NodeInputFrame[GraphValueT],
        /,
    ) -> _GraphValues[GraphValueT] | GraphOutcome[GraphValueT]:
        inputs = _make_node_inputs(frame, self.contract.bindings)
        materialized = self.contract.input_materializer(inputs)
        typed_input = admit_exact(
            materialized,
            self.contract.input_descriptor,
            kind="typed node materialized input",
        )
        result = await self.contract.operation(typed_input)
        if type(result) in (_GraphSuccessOutcome, _GraphFailureOutcome, _GraphInterruptOutcome):
            return cast(GraphOutcome[GraphValueT], result)
        admitted = admit_exact(
            cast(OutputT, result),
            self.contract.output.descriptor,
            kind=f"typed node output {self.contract.output.name!r}",
        )
        published = self.contract.output_publisher(admitted)
        if type(published) is not _GraphValues:
            raise GraphValueAdmissionError("typed node output publisher must return Graph.Values")
        return cast(_GraphValues[GraphValueT], published)


def make_typed_node_invoker(
    contract: NodeContract[GraphValueT, InputT, OutputT],
) -> TypedNodeInvoker[GraphValueT]:
    """Erase one DTO pair only after preserving it inside the typed adapter."""

    return _TypedNodeAdapter(contract)


async def invoke_node(
    definition: CallableNodeDefinition[GraphValueT],
    frame: NodeInputFrame[GraphValueT],
) -> _GraphValues[GraphValueT] | GraphOutcome[GraphValueT]:
    """Invoke one ordinary node through exactly one scheduler-facing seam."""

    typed = definition.typed_invoker
    if typed is not None:
        return await typed(frame)
    operation = cast(NodeCallable[GraphValueT], definition.operation)
    return await operation(_public_node_input(frame))


__all__: list[str] = []

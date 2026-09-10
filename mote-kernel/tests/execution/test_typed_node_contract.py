from dataclasses import dataclass, replace
from typing import Never, TypeAlias, cast

import pytest
from tests.execution.engine.factories import activation_config

from mote_kernel.config import ConfigActivation
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.node import _make_node_inputs
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    NodeOutputRef,
    NominalTypeDescriptor,
    TypedInputBinding,
    canonical_nominal_type,
    normalize_output_declarations,
)
from mote_kernel.execution.graph.values import NamedValue, _make_node_input_frame
from mote_kernel.execution.node_adapter import make_typed_node_assembly
from mote_kernel.state.graph_state import GraphNodeId


@dataclass(frozen=True, slots=True)
class Left:
    value: str


@dataclass(frozen=True, slots=True)
class Right:
    value: int


@dataclass(frozen=True, slots=True)
class PairRequest:
    left: Left
    right: Right


@dataclass(frozen=True, slots=True)
class Combined:
    value: str


@dataclass(frozen=True, slots=True)
class WrongInput:
    value: str


@dataclass(frozen=True, slots=True)
class WrongOutput:
    value: str


@dataclass(frozen=True, slots=True)
class EmptyInput:
    """Input DTO for a typed node with no graph slots."""


PipelineValue: TypeAlias = Left | Right | PairRequest | Combined | WrongInput | WrongOutput | EmptyInput


@pytest.mark.asyncio
async def test_typed_node_materializes_one_dto_and_publishes_one_typed_output() -> None:
    graph = Graph[PipelineValue]("typed.linear")
    left = Graph.bind("left", Graph.graph_input("left", Left))
    right = Graph.bind("right", Graph.graph_input("right", Right))
    received: list[PairRequest] = []

    async def combine(request: PairRequest) -> Combined:
        received.append(request)
        return Combined(f"{request.left.value}:{request.right.value}")

    output = graph.add_node(
        "combine",
        combine,
        inputs=(right, left),
        input_type=PairRequest,
        materialize=lambda values: PairRequest(values.get(left), values.get(right)),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("combine", Graph.END)
    graph.set_outputs({"result": output})

    result = await graph.run(Graph.values(left=Left("value"), right=Right(7)))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == Combined("value:7")
    assert received == [PairRequest(Left("value"), Right(7))]


@pytest.mark.asyncio
async def test_typed_node_publishes_config_activation_metadata_without_changing_output_type() -> None:
    graph = Graph[PipelineValue]("typed.config-activation")
    source = Graph.bind("source", Graph.graph_input("source", Left))
    runtime_config = activation_config(1)

    async def operation(value: Left) -> ConfigActivation[Combined]:
        return ConfigActivation(Combined(value.value), runtime_config)

    output = graph.add_node(
        "node",
        operation,
        inputs=(source,),
        input_type=Left,
        materialize=lambda values: values.get(source),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("node", Graph.END)
    graph.set_outputs({"result": output})

    result = await graph.run(Graph.values(source=Left("value")), activation_config=runtime_config)

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == Combined("value")


@pytest.mark.asyncio
async def test_typed_node_rejects_wrong_materialized_outer_class_before_operation() -> None:
    graph = Graph[PipelineValue]("typed.bad-input")
    source = Graph.bind("source", Graph.graph_input("source", Left))
    called = False

    async def operation(_request: PairRequest) -> Combined:
        nonlocal called
        called = True
        return Combined("unreachable")

    def malformed_materializer(_values: Graph.Inputs[PipelineValue]) -> PairRequest:
        return cast(PairRequest, WrongInput("wrong"))

    output = graph.add_node(
        "node",
        operation,
        inputs=(source,),
        input_type=PairRequest,
        materialize=malformed_materializer,
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("node", Graph.END)
    graph.set_outputs({"result": output})

    with pytest.raises(Graph.ValueAdmissionError, match="materialized input"):
        await graph.run(Graph.values(source=Left("value")))

    assert called is False


@pytest.mark.asyncio
async def test_typed_node_rejects_wrong_operation_output_before_publication() -> None:
    graph = Graph[PipelineValue]("typed.bad-output")
    source = Graph.bind("source", Graph.graph_input("source", Left))

    async def malformed(_request: Left) -> Combined:
        return cast(Combined, WrongOutput("wrong"))

    output = graph.add_node(
        "node",
        malformed,
        inputs=(source,),
        input_type=Left,
        materialize=lambda values: values.get(source),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("node", Graph.END)
    graph.set_outputs({"result": output})

    with pytest.raises(Graph.ValueAdmissionError, match="typed node output 'result'"):
        await graph.run(Graph.values(source=Left("value")))


def test_typed_binding_rejects_an_address_only_output_reference() -> None:
    with pytest.raises(Graph.ValidationError, match="typed Graph handle"):
        Graph.bind("value", Graph.node_output("source", "value"))


def test_typed_node_rejects_duplicate_binding_slots_during_assembly() -> None:
    graph = Graph[PipelineValue]("typed.duplicate-input")
    source = Graph.bind("source", Graph.graph_input("source", Left))

    async def operation(value: Left) -> Combined:
        return Combined(value.value)

    with pytest.raises(Graph.ValidationError, match="unique canonical destination"):
        graph.add_node(
            "node",
            operation,
            inputs=(source, source),
            input_type=Left,
            materialize=lambda values: values.get(source),
            output_name="result",
            output_type=Combined,
        )


def test_typed_assembly_reuses_one_descriptor_across_definition_and_output_handle() -> None:
    binding = Graph.bind("source", Graph.graph_input("source", Left))

    async def operation(value: Left) -> Combined:
        return Combined(value.value)

    assembly = make_typed_node_assembly(
        GraphNodeId("node"),
        operation,
        (binding,),
        Left,
        lambda values: values.get(binding),
        "result",
        Combined,
        (),
    )

    definition = assembly.definition
    descriptor = definition.outputs.entries[0].descriptor
    assert assembly.output_ref.descriptor is descriptor
    assert definition.inputs.entries[0].source is binding.source


def test_typed_assembly_rejects_malformed_bindings_and_materializer_at_its_boundary() -> None:
    valid = Graph.bind("source", Graph.graph_input("source", Left))
    non_concrete_descriptor = cast(NominalTypeDescriptor[Left], NominalTypeDescriptor(object))
    malformed: tuple[tuple[TypedInputBinding[PipelineValue], str], ...] = (
        (
            TypedInputBinding(cast(str, object()), valid.source),
            "input name",
        ),
        (
            TypedInputBinding(
                "source",
                cast(GraphInputRef[Left], object()),
            ),
            "malformed input source",
        ),
        (
            TypedInputBinding(
                "source",
                GraphInputRef("source", non_concrete_descriptor),
            ),
            "non-concrete descriptor",
        ),
        (
            TypedInputBinding(
                "source",
                NodeOutputRef[Left](GraphNodeId("source"), "value"),
            ),
            "lacks one concrete descriptor",
        ),
    )

    async def operation(value: Left) -> Combined:
        return Combined(value.value)

    for binding, match in malformed:
        with pytest.raises(Graph.ValidationError, match=match):
            make_typed_node_assembly(
                GraphNodeId("node"),
                operation,
                (cast(TypedInputBinding[PipelineValue], binding),),
                Left,
                lambda values: values.get(valid),
                "result",
                Combined,
                (),
            )

    with pytest.raises(Graph.ValidationError, match="must be a tuple"):
        make_typed_node_assembly(
            GraphNodeId("node"),
            operation,
            cast(tuple[TypedInputBinding[PipelineValue], ...], [valid]),
            Left,
            lambda values: values.get(valid),
            "result",
            Combined,
            (),
        )

    graph = Graph[PipelineValue]("typed.bad-assembly")
    with pytest.raises(Graph.ValidationError, match=r"Graph\.bind"):
        graph.add_node(
            "node",
            operation,
            inputs=(cast(TypedInputBinding[PipelineValue], object()),),
            input_type=Left,
            materialize=lambda values: values.get(valid),
            output_name="result",
            output_type=Combined,
        )
    with pytest.raises(Graph.ValidationError, match="callable materializer"):
        graph.add_node(
            "node",
            operation,
            inputs=(cast(TypedInputBinding[PipelineValue], valid),),
            input_type=Left,
            materialize=cast(Never, object()),
            output_name="result",
            output_type=Combined,
        )


def test_typed_input_view_enforces_its_owner_seal() -> None:
    frame = _make_node_input_frame(
        (NamedValue("source", Left("value")),),
        normalize_output_declarations({"source": Left}),
    )
    with pytest.raises(Graph.ValueAdmissionError, match="execution owner"):
        Graph.Inputs(
            _frame=frame,
            _bindings=(),
            _seal=cast(Never, object()),
        )


def test_typed_input_view_rejects_a_declared_source_without_a_descriptor() -> None:
    frame = _make_node_input_frame(
        (NamedValue("source", Left("value")),),
        normalize_output_declarations({"source": Left}),
    )
    binding = TypedInputBinding[PipelineValue](
        "source",
        NodeOutputRef(GraphNodeId("producer"), "source"),
    )
    inputs = _make_node_inputs(frame, (binding,))

    with pytest.raises(Graph.ValueAdmissionError, match="lacks its nominal descriptor"):
        inputs.get(binding)


@pytest.mark.asyncio
async def test_typed_materializer_rejects_an_equal_but_foreign_binding() -> None:
    graph = Graph[PipelineValue]("typed.equal-foreign-binding")
    declared = Graph.bind("declared", Graph.graph_input("declared", Left))
    foreign = replace(declared)
    assert foreign == declared
    assert foreign is not declared

    async def operation(value: Left) -> Combined:
        return Combined(value.value)

    output = graph.add_node(
        "node",
        operation,
        inputs=(declared,),
        input_type=Left,
        materialize=lambda values: values.get(foreign),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("node", Graph.END)
    graph.set_outputs({"result": output})

    with pytest.raises(Graph.ValueAdmissionError, match="undeclared input slot"):
        await graph.run(Graph.values(declared=Left("value")))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    (Graph.failure("typed failure"), Graph.interrupt(b"typed interrupt")),
    ids=("failure", "interrupt"),
)
async def test_typed_adapter_passes_existing_graph_outcomes_through_unchanged(
    outcome: Graph.FailureOutcome | Graph.InterruptOutcome,
) -> None:
    binding = Graph.bind("source", Graph.graph_input("source", Left))

    async def operation(
        _value: Left,
    ) -> Combined | Graph.FailureOutcome | Graph.InterruptOutcome:
        return outcome

    assembly = make_typed_node_assembly(
        GraphNodeId("node"),
        operation,
        (binding,),
        Left,
        lambda values: values.get(binding),
        "result",
        Combined,
        (),
    )
    frame = _make_node_input_frame(
        (NamedValue("source", Left("value")),),
        normalize_output_declarations({"source": Left}),
    )

    assert await assembly.definition.invoker(frame) is outcome


@pytest.mark.asyncio
async def test_typed_materializer_cannot_read_an_undeclared_binding() -> None:
    graph = Graph[PipelineValue]("typed.foreign-binding")
    declared = Graph.bind("declared", Graph.graph_input("declared", Left))
    foreign = Graph.bind("foreign", Graph.graph_input("foreign", Right))

    async def operation(value: Right) -> Combined:
        return Combined(str(value.value))

    output = graph.add_node(
        "node",
        operation,
        inputs=(declared,),
        input_type=Right,
        materialize=lambda values: values.get(foreign),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("node", Graph.END)
    graph.set_outputs({"result": output})

    with pytest.raises(Graph.ValueAdmissionError, match="undeclared input slot"):
        await graph.run(Graph.values(declared=Left("value")))


@pytest.mark.asyncio
async def test_compiler_rejects_a_typed_output_handle_with_a_foreign_descriptor() -> None:
    graph = Graph[PipelineValue]("typed.forged-output-ref")
    source = Graph.bind("source", Graph.graph_input("source", Left))

    async def produce(value: Left) -> Combined:
        return Combined(value.value)

    produced = graph.add_node(
        "produce",
        produce,
        inputs=(source,),
        input_type=Left,
        materialize=lambda values: values.get(source),
        output_name="result",
        output_type=Combined,
    )
    wrong_descriptor = cast(
        NominalTypeDescriptor[Combined],
        canonical_nominal_type(WrongOutput),
    )
    forged = replace(produced, descriptor=wrong_descriptor)
    consumed = Graph.bind("value", forged)

    async def consume(value: Combined) -> Combined:
        return value

    output = graph.add_node(
        "consume",
        consume,
        inputs=(consumed,),
        input_type=Combined,
        materialize=lambda values: values.get(consumed),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("produce", "consume")
    graph.add_edge("consume", Graph.END)
    graph.set_outputs({"result": output})

    with pytest.raises(Graph.ValidationError, match="does not match its declared exact type"):
        await graph.run(Graph.values(source=Left("value")))


@pytest.mark.asyncio
async def test_typed_success_outcome_preserves_route_and_output() -> None:
    graph = Graph[PipelineValue]("typed.routed-success")
    source = Graph.bind("source", Graph.graph_input("source", Left))

    async def choose(value: Left) -> Graph.SuccessOutcome[Combined]:
        return Graph.success(Graph.values(result=Combined(value.value)), route="continue")

    chosen = graph.add_node(
        "choose",
        choose,
        inputs=(source,),
        input_type=Left,
        materialize=lambda values: values.get(source),
        output_name="result",
        output_type=Combined,
    )
    consumed = Graph.bind("value", chosen)

    async def consume(value: Combined) -> Combined:
        return Combined(value.value.upper())

    output = graph.add_node(
        "consume",
        consume,
        inputs=(consumed,),
        input_type=Combined,
        materialize=lambda values: values.get(consumed),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("choose", "continue", "consume")
    graph.add_edge("consume", Graph.END)
    graph.set_outputs({"result": output})

    result = await graph.run(Graph.values(source=Left("value")))

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == Combined("VALUE")


@pytest.mark.asyncio
async def test_typed_predecessor_binding_reads_the_selected_branch_publication() -> None:
    graph = Graph[PipelineValue]("typed.predecessor")

    async def choose(_value: Graph.Values[PipelineValue]) -> Graph.Outcome[PipelineValue]:
        return Graph.success(Graph.values(), route="left")

    graph.add_node("choose", choose, inputs={}, outputs={})

    async def left(_value: EmptyInput) -> Right:
        return Right(1)

    async def right(_value: EmptyInput) -> Right:
        return Right(2)

    left_output = graph.add_node(
        "left",
        left,
        inputs=(),
        input_type=EmptyInput,
        materialize=lambda _values: EmptyInput(),
        output_name="value",
        output_type=Right,
    )
    graph.add_node(
        "right",
        right,
        inputs=(),
        input_type=EmptyInput,
        materialize=lambda _values: EmptyInput(),
        output_name="value",
        output_type=Right,
    )
    predecessor = Graph.bind("previous", Graph.node_output(left_output))

    async def consume(value: Right) -> Combined:
        return Combined(str(value.value))

    result_output = graph.add_node(
        "consume",
        consume,
        inputs=(predecessor,),
        input_type=Right,
        materialize=lambda values: values.get(predecessor),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("choose", "left", "left")
    graph.add_edge("choose", "right", "right")
    graph.add_edge("left", "consume")
    graph.add_edge("right", "consume")
    graph.add_edge("consume", Graph.END)
    graph.set_outputs({"result": result_output})

    result = await graph.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert result.outputs["result"] == Combined("1")


@pytest.mark.asyncio
async def test_compiler_rejects_a_typed_predecessor_handle_with_the_wrong_descriptor() -> None:
    graph = Graph[PipelineValue]("typed.predecessor-descriptor")

    async def produce(_value: EmptyInput) -> Right:
        return Right(1)

    produced = graph.add_node(
        "produce",
        produce,
        inputs=(),
        input_type=EmptyInput,
        materialize=lambda _values: EmptyInput(),
        output_name="value",
        output_type=Right,
    )
    forged = cast(
        NodeOutputRef[Combined],
        replace(produced, descriptor=canonical_nominal_type(Combined)),
    )
    predecessor = Graph.bind("previous", Graph.node_output(forged))

    async def consume(value: Combined) -> Combined:
        return value

    output = graph.add_node(
        "consume",
        consume,
        inputs=(predecessor,),
        input_type=Combined,
        materialize=lambda values: values.get(predecessor),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("produce", "consume")
    graph.add_edge("consume", Graph.END)
    graph.set_outputs({"result": output})

    with pytest.raises(Graph.ValidationError, match="typed predecessor input"):
        await graph.run(Graph.values())


@pytest.mark.asyncio
async def test_typed_nested_completion_route_is_consumed_by_the_parent_graph() -> None:
    child = Graph[PipelineValue]("typed.nested.child")

    async def choose(_value: EmptyInput) -> Right | Graph.SuccessOutcome[Right]:
        return Graph.success(Graph.values(value=Right(3)), route="chosen")

    child_output = child.add_node(
        "choose",
        choose,
        inputs=(),
        input_type=EmptyInput,
        materialize=lambda _values: EmptyInput(),
        output_name="value",
        output_type=Right,
    )
    child.add_edge("choose", Graph.END)
    child.set_outputs({"value": child_output})

    parent = Graph[PipelineValue]("typed.nested.parent")
    parent.add_node("child", child, inputs={})
    called = False

    async def consumer(_value: Graph.Values[PipelineValue]) -> Graph.Values[PipelineValue]:
        nonlocal called
        called = True
        return Graph.values()

    parent.add_node("consumer", consumer, inputs={}, outputs={})
    parent.add_edge("child", "chosen", "consumer")
    parent.add_edge("consumer", Graph.END)
    parent.set_outputs({})

    result = await parent.run(Graph.values())

    assert isinstance(result, Graph.CompletedResult)
    assert called is True


@pytest.mark.asyncio
async def test_typed_node_preserves_the_operation_exception_instance() -> None:
    graph = Graph[PipelineValue]("typed.exception")
    source = Graph.bind("source", Graph.graph_input("source", Left))
    failure = RuntimeError("domain failure")

    async def operation(_value: Left) -> Combined:
        raise failure

    output = graph.add_node(
        "node",
        operation,
        inputs=(source,),
        input_type=Left,
        materialize=lambda values: values.get(source),
        output_name="result",
        output_type=Combined,
    )
    graph.add_edge("node", Graph.END)
    graph.set_outputs({"result": output})

    with pytest.raises(RuntimeError) as captured:
        await graph.run(Graph.values(source=Left("value")))

    assert captured.value is failure

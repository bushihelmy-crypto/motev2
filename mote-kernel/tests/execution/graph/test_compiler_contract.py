from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias, TypeVar, cast

import pytest
from tests.execution.graph.factories import compiled_join

import mote_kernel.execution.graph.compiler as compiler_module
from mote_kernel.execution import Graph
from mote_kernel.execution.errors import (
    DuplicateBoundaryError,
    GraphValidationError,
    UnknownNodeError,
)
from mote_kernel.execution.graph.compiler import compile_graph
from mote_kernel.execution.graph.constants import END
from mote_kernel.execution.graph.definition import GraphDefinition, NestedGraphNodeDefinition
from mote_kernel.execution.graph.edge import ConditionalEdge, DirectEdge, Edge, JoinEdge
from mote_kernel.execution.graph.node import CallableNodeDefinition
from mote_kernel.execution.graph.ports import (
    GraphInputRef,
    GraphOutputDeclarations,
    NodeOutputPort,
    NodeOutputRef,
    PublicationSelection,
    PublicationSelectionKind,
    normalize_graph_output_declarations,
    normalize_input_bindings,
    normalize_output_declarations,
)
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphNodeId,
    GraphRouteId,
)

GraphValueT = TypeVar("GraphValueT")
RawActivationGate: TypeAlias = tuple[tuple[GraphNodeId, GraphRouteId | None], ...]


@dataclass(frozen=True, slots=True)
class _RouteRequirementProofView:
    requirements: tuple[tuple[GraphNodeId, frozenset[GraphRouteId]], ...]
    exact: bool


class _CompilerPrivateView(Protocol):
    _can_reach: Callable[..., bool]
    _gates_can_coexist: Callable[..., bool]
    _repeatable_nodes: Callable[..., frozenset[GraphNodeId]]

    @staticmethod
    def can_reach(
        module: object,
        source: GraphNodeId,
        target: GraphNodeId,
        successors: dict[GraphNodeId, set[GraphNodeId]],
    ) -> bool:
        return cast(_CompilerPrivateView, module)._can_reach(source, target, successors)

    @staticmethod
    def gates_can_coexist(
        module: object,
        first: RawActivationGate,
        second: RawActivationGate,
        requirements: dict[GraphNodeId, _RouteRequirementProofView] | None = None,
        conditional_targets: dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]] | None = None,
    ) -> bool:
        function = cast(
            Callable[
                [
                    RawActivationGate,
                    RawActivationGate,
                    dict[GraphNodeId, _RouteRequirementProofView] | None,
                    dict[GraphNodeId, dict[GraphRouteId, GraphNodeId]] | None,
                ],
                bool,
            ],
            cast(_CompilerPrivateView, module)._gates_can_coexist,
        )
        return function(first, second, requirements, conditional_targets)

    @staticmethod
    def repeatable_nodes(
        module: object,
        entries: tuple[GraphNodeId, ...],
        activation_gates: dict[GraphNodeId, list[RawActivationGate]],
        successors: dict[GraphNodeId, set[GraphNodeId]],
    ) -> frozenset[GraphNodeId]:
        function = cast(
            Callable[
                [
                    tuple[GraphNodeId, ...],
                    dict[GraphNodeId, list[RawActivationGate]],
                    dict[GraphNodeId, set[GraphNodeId]],
                ],
                frozenset[GraphNodeId],
            ],
            cast(_CompilerPrivateView, module)._repeatable_nodes,
        )
        return function(entries, activation_gates, successors)


PipelineValue: TypeAlias = str | int


async def identity(values: Graph.Values[PipelineValue], /) -> Graph.Values[PipelineValue]:
    return values


def node(
    node_id: str,
    *,
    inputs: Mapping[str, GraphInputRef[PipelineValue] | NodeOutputRef],
    outputs: Mapping[str, type[PipelineValue]],
) -> CallableNodeDefinition[PipelineValue]:
    return CallableNodeDefinition(
        GraphNodeId(node_id),
        identity,
        normalize_input_bindings(inputs),
        normalize_output_declarations(outputs),
    )


def definition(
    nodes: tuple[
        CallableNodeDefinition[PipelineValue] | NestedGraphNodeDefinition[PipelineValue],
        ...,
    ],
    *,
    edges: tuple[Edge, ...] = (),
    entries: tuple[str, ...] = (),
    outputs: GraphOutputDeclarations[PipelineValue] | None = None,
    definition_id: str = "compiler.contract",
) -> GraphDefinition[PipelineValue]:
    return GraphDefinition(
        GraphDefinitionId(definition_id),
        GraphDefinitionVersion(1),
        nodes,
        edges,
        tuple(GraphNodeId(entry) for entry in entries),
        normalize_graph_output_declarations({}) if outputs is None else outputs,
    )


def test_compiler_resolves_a_later_named_output_declaration() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"first": str, "second": str},
    )
    consumer = node(
        "consumer",
        inputs={"selected": Graph.node_output("source", "second")},
        outputs={},
    )

    compiled = compile_graph(
        definition(
            (source, consumer),
            edges=(DirectEdge(GraphNodeId("source"), GraphNodeId("consumer")),),
        )
    )

    binding = compiled.transition.materializations[GraphNodeId("consumer")].bindings.entries[0]
    assert isinstance(binding.source, NodeOutputPort)
    assert binding.source.output_name == "second"


def test_compiler_rejects_an_unknown_output_port() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    consumer = node(
        "consumer",
        inputs={"value": Graph.node_output("source", "missing")},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="unknown output port"):
        compile_graph(definition((source, consumer)))


def test_compiler_rejects_conflicting_graph_input_exact_types() -> None:
    string_node = node(
        "string",
        inputs={"value": Graph.graph_input("shared", str)},
        outputs={},
    )
    integer_node = node(
        "integer",
        inputs={"value": Graph.graph_input("shared", int)},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="conflicting exact type"):
        compile_graph(definition((string_node, integer_node)))


def test_compiler_rejects_a_node_binding_its_own_output() -> None:
    recursive = node(
        "recursive",
        inputs={"value": Graph.node_output("recursive", "value")},
        outputs={"value": str},
    )

    with pytest.raises(GraphValidationError, match="cannot bind its own output"):
        compile_graph(definition((recursive,)))


def test_compiler_rejects_a_value_source_from_an_unknown_node() -> None:
    consumer = node(
        "consumer",
        inputs={"value": Graph.node_output("unknown", "value")},
        outputs={},
    )

    with pytest.raises(UnknownNodeError, match="value source"):
        compile_graph(definition((consumer,)))


def test_compiler_rejects_an_ordinary_data_cycle() -> None:
    left = node(
        "left",
        inputs={"value": Graph.node_output("right", "value")},
        outputs={"value": str},
    )
    right = node(
        "right",
        inputs={"value": Graph.node_output("left", "value")},
        outputs={"value": str},
    )

    with pytest.raises(GraphValidationError, match="data cycle"):
        compile_graph(definition((left, right)))


def test_compiler_requires_explicit_control_for_node_output_consumers() -> None:
    alpha = node("alpha", inputs={}, outputs={"first": str, "second": str})
    zeta = node("zeta", inputs={}, outputs={"value": str})
    consumer = node(
        "consumer",
        inputs={
            "first": Graph.node_output("alpha", "first"),
            "second": Graph.node_output("alpha", "second"),
            "zeta": Graph.node_output("zeta", "value"),
        },
        outputs={},
    )

    with pytest.raises(
        GraphValidationError,
        match=r"node 'consumer' consumes node outputs from \('alpha', 'zeta'\) but has no incoming control edge",
    ):
        compile_graph(definition((zeta, consumer, alpha)))


def test_compiler_accepts_data_binding_and_direct_control_for_the_same_pair() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )

    compiled = compile_graph(
        definition(
            (source, target),
            edges=(DirectEdge(GraphNodeId("source"), GraphNodeId("target")),),
        )
    )

    assert compiled.transition.direct_targets[GraphNodeId("source")] == (GraphNodeId("target"),)
    binding = compiled.transition.materializations[GraphNodeId("target")].bindings.entries[0]
    assert binding.source == NodeOutputPort((), GraphNodeId("source"), "value")


def test_compiler_rejects_explicit_start_that_duplicates_automatic_entry() -> None:
    entry = node("entry", inputs={}, outputs={})

    with pytest.raises(DuplicateBoundaryError, match="automatic entry"):
        compile_graph(definition((entry,), entries=("entry",)))


def test_compiler_rejects_explicit_start_target_requiring_node_output() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="explicit START target"):
        compile_graph(definition((source, target), entries=("target",)))


def test_compiler_rejects_control_path_that_can_reach_its_required_producer() -> None:
    controller = node("controller", inputs={}, outputs={})
    source = node("source", inputs={}, outputs={"value": str})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    edges = (
        DirectEdge(GraphNodeId("controller"), GraphNodeId("target")),
        DirectEdge(GraphNodeId("target"), GraphNodeId("source")),
    )

    with pytest.raises(GraphValidationError, match="not guaranteed before controlled"):
        compile_graph(definition((controller, source, target), edges=edges))


def test_compiler_rejects_control_gate_without_required_producer_guarantee() -> None:
    controller = node("controller", inputs={}, outputs={})
    source = node("source", inputs={}, outputs={"value": str})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="can activate before required producers"):
        compile_graph(
            definition(
                (controller, source, target),
                edges=(DirectEdge(GraphNodeId("controller"), GraphNodeId("target")),),
            )
        )


def test_compiler_accepts_a_coordinator_gate_after_the_required_producer() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    coordinator = node("coordinator", inputs={}, outputs={})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )

    compiled = compile_graph(
        definition(
            (target, coordinator, source),
            edges=(
                DirectEdge(GraphNodeId("source"), GraphNodeId("coordinator")),
                DirectEdge(GraphNodeId("coordinator"), GraphNodeId("target")),
            ),
        )
    )

    assert compiled.transition.direct_targets[GraphNodeId("coordinator")] == (GraphNodeId("target"),)


def test_compiler_accepts_a_join_gate_for_all_required_producers() -> None:
    left = node("left", inputs={}, outputs={"value": str})
    right = node("right", inputs={}, outputs={"value": str})
    target = node(
        "target",
        inputs={
            "left": Graph.node_output("left", "value"),
            "right": Graph.node_output("right", "value"),
        },
        outputs={},
    )
    edge = JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("target"))

    compiled = compile_graph(definition((target, right, left), edges=(edge,)))

    assert compiled.transition.joins_by_source[GraphNodeId("left")] == (compiled_join(("left", "right"), "target"),)
    assert len(compiled.transition.materializations[GraphNodeId("target")].bindings.entries) == 2


def test_cyclic_join_inputs_use_the_compiled_occurrence_offsets() -> None:
    tick = node("tick", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={"value": str})
    right = node("right", inputs={}, outputs={"value": str})
    joined = node(
        "joined",
        inputs={
            "left": Graph.node_output("left", "value"),
            "right": Graph.node_output("right", "value"),
        },
        outputs={},
    )
    compiled = compile_graph(
        definition(
            (tick, left, right, joined),
            edges=(
                DirectEdge(GraphNodeId("tick"), GraphNodeId("left")),
                DirectEdge(GraphNodeId("tick"), GraphNodeId("right")),
                JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
                ConditionalEdge(GraphNodeId("joined"), GraphRouteId("again"), GraphNodeId("tick")),
                ConditionalEdge(GraphNodeId("joined"), GraphRouteId("done"), END),
            ),
            entries=("tick",),
        )
    )

    bindings = compiled.transition.materializations[GraphNodeId("joined")].bindings.entries
    assert tuple(binding.publication for binding in bindings) == (
        PublicationSelection(PublicationSelectionKind.RELATIVE, 1),
        PublicationSelection(PublicationSelectionKind.RELATIVE, 1),
    )


def test_compiler_requires_nested_inputs_to_match_child_boundary_exactly() -> None:
    child_step = node(
        "child-step",
        inputs={"child_input": Graph.graph_input("child_input", str)},
        outputs={},
    )
    child = definition((child_step,), definition_id="compiler.child")
    nested = NestedGraphNodeDefinition(
        GraphNodeId("nested"),
        child,
        normalize_input_bindings({"wrong": Graph.graph_input("wrong", str)}),
    )

    with pytest.raises(GraphValidationError, match="exactly match child boundary"):
        compile_graph(definition((nested,)))


def test_compiler_uses_relative_selection_for_loop_producer_with_direct_activation() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    compiled = compile_graph(
        definition(
            (source, target),
            edges=(
                DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
                DirectEdge(GraphNodeId("source"), GraphNodeId("target")),
            ),
            entries=("source",),
        )
    )

    selection = compiled.transition.materializations[GraphNodeId("target")].bindings.entries[0].publication
    assert selection is not None
    assert selection.kind is PublicationSelectionKind.RELATIVE
    assert selection.superstep == 1


def test_compiler_uses_relative_selection_for_same_source_conditional_routes() -> None:
    source = node(
        "source",
        inputs={"value": Graph.graph_input("value", str)},
        outputs={"value": str},
    )
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    compiled = compile_graph(
        definition(
            (source, target),
            edges=(
                DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
                ConditionalEdge(GraphNodeId("source"), GraphRouteId("left"), GraphNodeId("target")),
                ConditionalEdge(GraphNodeId("source"), GraphRouteId("right"), GraphNodeId("target")),
            ),
            entries=("source",),
        )
    )

    selection = compiled.transition.materializations[GraphNodeId("target")].bindings.entries[0].publication
    assert selection is not None
    assert selection.kind is PublicationSelectionKind.RELATIVE
    assert selection.superstep == 1


def test_compiler_rejects_a_repeatable_join_source_before_publication_selection() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    gate = node("gate", inputs={}, outputs={})
    target = node(
        "target",
        inputs={"value": Graph.node_output("source", "value")},
        outputs={},
    )
    edges = (
        DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
        JoinEdge((GraphNodeId("source"), GraphNodeId("gate")), GraphNodeId("target")),
    )

    with pytest.raises(GraphValidationError, match="occurrence identity"):
        compile_graph(definition((source, gate, target), edges=edges, entries=("source",)))


def test_compiler_uses_relative_selection_for_loop_graph_output() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    compiled = compile_graph(
        definition(
            (source,),
            edges=(
                DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
                DirectEdge(GraphNodeId("source"), END),
            ),
            entries=("source",),
            outputs=normalize_graph_output_declarations({"value": Graph.node_output("source", "value")}),
        )
    )

    selection = compiled.transition.graph_outputs.entries[0].publication
    assert selection is not None
    assert selection.kind is PublicationSelectionKind.RELATIVE
    assert selection.superstep == 0


def test_compiler_rejects_repeatable_terminal_join_sources_before_output_selection() -> None:
    source = node("source", inputs={}, outputs={"value": str})
    gate = node("gate", inputs={}, outputs={})
    edges = (
        DirectEdge(GraphNodeId("source"), GraphNodeId("source")),
        DirectEdge(GraphNodeId("gate"), GraphNodeId("gate")),
        JoinEdge((GraphNodeId("source"), GraphNodeId("gate")), END),
    )

    with pytest.raises(GraphValidationError, match="occurrence identity"):
        compile_graph(
            definition(
                (source, gate),
                edges=edges,
                entries=("source", "gate"),
                outputs=normalize_graph_output_declarations({"value": Graph.node_output("source", "value")}),
            )
        )


def test_compiler_rejects_a_data_publication_without_one_activation_coordinate() -> None:
    loop = node("loop", inputs={}, outputs={"value": str})
    coordinator = node("coordinator", inputs={}, outputs={})
    target = node(
        "target",
        inputs={"value": Graph.node_output("loop", "value")},
        outputs={},
    )

    with pytest.raises(GraphValidationError, match="no unique activation coordinate"):
        compile_graph(
            definition(
                (loop, coordinator, target),
                edges=(
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                    ConditionalEdge(
                        GraphNodeId("loop"),
                        GraphRouteId("done"),
                        GraphNodeId("coordinator"),
                    ),
                    DirectEdge(GraphNodeId("coordinator"), GraphNodeId("target")),
                ),
                entries=("loop",),
            )
        )


def test_compiler_rejects_a_graph_output_without_one_completion_coordinate() -> None:
    loop = node("loop", inputs={}, outputs={"value": str})
    finish = node("finish", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="no unique completion activation coordinate"):
        compile_graph(
            definition(
                (loop, finish),
                edges=(
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("continue"), GraphNodeId("loop")),
                    ConditionalEdge(GraphNodeId("loop"), GraphRouteId("done"), GraphNodeId("finish")),
                ),
                entries=("loop",),
                outputs=normalize_graph_output_declarations({"value": Graph.node_output("loop", "value")}),
            )
        )


def test_compiler_rejects_output_not_guaranteed_on_every_terminal_branch() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={"value": str})
    right = node("right", inputs={}, outputs={})
    edges: tuple[Edge, ...] = (
        ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
        ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
    )

    with pytest.raises(GraphValidationError, match="not guaranteed before every successful completion"):
        compile_graph(
            definition(
                (decision, left, right),
                edges=edges,
                outputs=normalize_graph_output_declarations({"value": Graph.node_output("left", "value")}),
            )
        )


def test_join_to_end_is_one_terminal_gate_for_output_guarantees() -> None:
    left = node("left", inputs={}, outputs={"value": str})
    right = node("right", inputs={}, outputs={})

    compiled = compile_graph(
        definition(
            (left, right),
            edges=(JoinEdge((GraphNodeId("left"), GraphNodeId("right")), END),),
            outputs=normalize_graph_output_declarations({"value": Graph.node_output("left", "value")}),
        )
    )

    assert compiled.transition.graph_outputs.entries[0].source == NodeOutputPort((), GraphNodeId("left"), "value")


def test_compiler_rejects_a_join_between_mutually_exclusive_routes() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})
    joined = node("joined", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="jointly satisfiable"):
        compile_graph(
            definition(
                (decision, left, right, joined),
                edges=(
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                    JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
                ),
            )
        )


def test_compiler_rejects_a_terminal_join_between_mutually_exclusive_routes() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="mutually exclusive activation sources"):
        compile_graph(
            definition(
                (decision, left, right),
                edges=(
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                    JoinEdge((GraphNodeId("left"), GraphNodeId("right")), END),
                ),
            )
        )


def test_compiler_rejects_a_join_that_can_receive_only_one_source_on_a_route() -> None:
    decision = node("decision", inputs={}, outputs={})
    selected = node("selected", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="partial source set"):
        compile_graph(
            definition(
                (decision, selected),
                edges=(
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("finish"), END),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("selected"), GraphNodeId("selected")),
                    JoinEdge((GraphNodeId("decision"), GraphNodeId("selected")), END),
                ),
            )
        )


def test_compiler_rejects_a_join_when_a_direct_path_can_coexist_with_the_selected_route() -> None:
    decision = node("decision", inputs={}, outputs={})
    always = node("always", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        compile_graph(
            definition(
                (decision, always, left, right),
                edges=(
                    DirectEdge(GraphNodeId("decision"), GraphNodeId("always")),
                    DirectEdge(GraphNodeId("decision"), GraphNodeId("right")),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                    JoinEdge((GraphNodeId("always"), GraphNodeId("right")), END),
                ),
            )
        )


def test_join_produced_and_ordinary_sources_cannot_merge_without_an_explicit_join() -> None:
    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        compile_graph(
            definition(
                tuple(
                    node(node_id, inputs={}, outputs={})
                    for node_id in ("source", "left", "right", "joined", "ordinary", "target")
                ),
                edges=(
                    DirectEdge(GraphNodeId("source"), GraphNodeId("left")),
                    DirectEdge(GraphNodeId("source"), GraphNodeId("right")),
                    DirectEdge(GraphNodeId("source"), GraphNodeId("ordinary")),
                    JoinEdge((GraphNodeId("left"), GraphNodeId("right")), GraphNodeId("joined")),
                    DirectEdge(GraphNodeId("joined"), GraphNodeId("target")),
                    DirectEdge(GraphNodeId("ordinary"), GraphNodeId("target")),
                ),
            )
        )


def test_compiler_rejects_cyclic_join_with_ambiguous_return_gates() -> None:
    decision = node("decision", inputs={}, outputs={})
    left = node("left", inputs={}, outputs={})
    right = node("right", inputs={}, outputs={})

    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        compile_graph(
            definition(
                (decision, left, right),
                edges=(
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                    ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                    DirectEdge(GraphNodeId("left"), GraphNodeId("decision")),
                    DirectEdge(GraphNodeId("right"), GraphNodeId("decision")),
                    JoinEdge((GraphNodeId("left"), GraphNodeId("right")), END),
                ),
                entries=("decision",),
            )
        )


def test_gate_coexistence_checks_route_requirements_and_simple_gate_shapes() -> None:
    source = GraphNodeId("source")
    left = GraphRouteId("left")
    right = GraphRouteId("right")
    requirements = {
        source: _RouteRequirementProofView(
            ((source, frozenset({left})),),
            True,
        )
    }
    conditional_targets = {source: {left: GraphNodeId("left"), right: GraphNodeId("right")}}

    assert not _CompilerPrivateView.gates_can_coexist(
        compiler_module,
        ((source, right),),
        ((source, left),),
        requirements,
        conditional_targets,
    )
    assert _CompilerPrivateView.gates_can_coexist(compiler_module, (), ((source, left),))
    assert not _CompilerPrivateView.gates_can_coexist(compiler_module, ((source, left),), ((source, right),))


def test_reachability_proof_fails_closed_at_boundary_shapes() -> None:
    source = GraphNodeId("source")
    target = GraphNodeId("target")

    assert not _CompilerPrivateView.can_reach(compiler_module, source, target, {source: set()})
    assert _CompilerPrivateView.can_reach(compiler_module, source, target, {source: {target}, target: set()})


def test_repeatable_node_propagation_reaches_acyclic_dependents() -> None:
    source = GraphNodeId("source")
    dependent = GraphNodeId("dependent")
    successors: dict[GraphNodeId, set[GraphNodeId]] = {
        source: {source},
        dependent: set(),
    }
    activation_gates: dict[GraphNodeId, list[RawActivationGate]] = {
        source: [((source, None),)],
        dependent: [((source, None),)],
    }

    assert _CompilerPrivateView.repeatable_nodes(compiler_module, (), activation_gates, successors) == frozenset(
        (source, dependent)
    )


def test_explicit_entry_with_an_incoming_gate_has_no_cross_cohort_join_identity() -> None:
    with pytest.raises(GraphValidationError, match="no provable occurrence identity"):
        compile_graph(
            definition(
                tuple(node(node_id, inputs={}, outputs={}) for node_id in ("a", "dependent", "other", "s", "target")),
                edges=(
                    DirectEdge(GraphNodeId("a"), GraphNodeId("s")),
                    DirectEdge(GraphNodeId("s"), GraphNodeId("dependent")),
                    JoinEdge((GraphNodeId("dependent"), GraphNodeId("other")), GraphNodeId("target")),
                ),
                entries=("s",),
            )
        )


def test_mutually_exclusive_incoming_routes_do_not_make_a_join_source_repeatable() -> None:
    compiled = compile_graph(
        definition(
            tuple(
                node(node_id, inputs={}, outputs={})
                for node_id in (
                    "decision",
                    "left",
                    "right",
                    "shared",
                    "other",
                    "target",
                )
            ),
            edges=(
                DirectEdge(GraphNodeId("decision"), GraphNodeId("other")),
                ConditionalEdge(GraphNodeId("decision"), GraphRouteId("left"), GraphNodeId("left")),
                ConditionalEdge(GraphNodeId("decision"), GraphRouteId("right"), GraphNodeId("right")),
                ConditionalEdge(GraphNodeId("left"), GraphRouteId("go"), GraphNodeId("shared")),
                ConditionalEdge(GraphNodeId("right"), GraphRouteId("go"), GraphNodeId("shared")),
                JoinEdge((GraphNodeId("other"), GraphNodeId("shared")), GraphNodeId("target")),
            ),
        )
    )

    assert compiled.transition.activation_gates[GraphNodeId("shared")] == (
        ((GraphNodeId("left"), frozenset({GraphRouteId("go")})),),
        ((GraphNodeId("right"), frozenset({GraphRouteId("go")})),),
    )


def test_branch_local_exit_cannot_leave_a_partial_join() -> None:
    with pytest.raises(GraphValidationError, match="partial source set"):
        compile_graph(
            definition(
                tuple(
                    node(node_id, inputs={}, outputs={})
                    for node_id in ("choose", "left", "ordinary", "right", "shared", "target")
                ),
                edges=(
                    ConditionalEdge(GraphNodeId("choose"), GraphRouteId("left"), GraphNodeId("left")),
                    ConditionalEdge(GraphNodeId("choose"), GraphRouteId("right"), GraphNodeId("right")),
                    DirectEdge(GraphNodeId("choose"), GraphNodeId("ordinary")),
                    ConditionalEdge(GraphNodeId("left"), GraphRouteId("go"), GraphNodeId("shared")),
                    ConditionalEdge(GraphNodeId("left"), GraphRouteId("stop"), END),
                    ConditionalEdge(GraphNodeId("right"), GraphRouteId("go"), GraphNodeId("shared")),
                    ConditionalEdge(GraphNodeId("right"), GraphRouteId("stop"), END),
                    JoinEdge((GraphNodeId("ordinary"), GraphNodeId("shared")), GraphNodeId("target")),
                ),
            )
        )


def test_coexisting_fanout_routes_require_an_explicit_join() -> None:
    with pytest.raises(GraphValidationError, match="multiple activation gates"):
        compile_graph(
            definition(
                tuple(node(node_id, inputs={}, outputs={}) for node_id in ("source", "left", "right", "target")),
                edges=(
                    DirectEdge(GraphNodeId("source"), GraphNodeId("left")),
                    DirectEdge(GraphNodeId("source"), GraphNodeId("right")),
                    DirectEdge(GraphNodeId("left"), GraphNodeId("target")),
                    DirectEdge(GraphNodeId("right"), GraphNodeId("target")),
                ),
            )
        )


def test_compiler_rejects_a_graph_without_any_entry() -> None:
    with pytest.raises(GraphValidationError, match="requires at least one"):
        compile_graph(
            definition(
                (
                    node("a", inputs={}, outputs={}),
                    node("b", inputs={}, outputs={}),
                ),
                edges=(
                    DirectEdge(GraphNodeId("a"), GraphNodeId("b")),
                    DirectEdge(GraphNodeId("b"), GraphNodeId("a")),
                ),
            )
        )


def test_compiler_rejects_nodes_unreachable_from_the_declared_entry() -> None:
    with pytest.raises(GraphValidationError, match="unreachable nodes"):
        compile_graph(
            definition(
                tuple(node(node_id, inputs={}, outputs={}) for node_id in ("a", "b", "c")),
                edges=(
                    DirectEdge(GraphNodeId("b"), GraphNodeId("a")),
                    DirectEdge(GraphNodeId("b"), GraphNodeId("c")),
                    DirectEdge(GraphNodeId("c"), GraphNodeId("b")),
                ),
                entries=("a",),
            )
        )

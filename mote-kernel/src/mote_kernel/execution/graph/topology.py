"""Compiled immutable graph plans shared by runtime and recovery."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.errors import SnapshotMismatchError
from mote_kernel.execution.graph.definition import GraphNode
from mote_kernel.execution.graph.ports import (
    ActivationGate,
    DefinitionScope,
    FrameDescriptor,
    GraphOutputBindings,
    MaterializationPlan,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import (
    GraphActivationIdentity,
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphJoinIdentity,
    GraphJoinOccurrenceIdentity,
    GraphNodeId,
    GraphRouteId,
)

KeyT = TypeVar("KeyT", bound=str)
ValueT_co = TypeVar("ValueT_co", covariant=True)
GraphValueT = TypeVar("GraphValueT")


@dataclass(frozen=True, slots=True)
class FrozenMap(Mapping[KeyT, ValueT_co], Generic[KeyT, ValueT_co]):
    entries: tuple[tuple[KeyT, ValueT_co], ...]

    def __getitem__(self, key: KeyT) -> ValueT_co:
        for candidate, value in self.entries:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[KeyT]:
        return (key for key, _value in self.entries)

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True, slots=True)
class CompiledJoin:
    """One Join definition and its source-to-target occurrence projection."""

    identity: GraphJoinIdentity
    source_target_offsets: tuple[tuple[GraphNodeId, int], ...]

    def __post_init__(self) -> None:
        sources = tuple(source for source, _offset in self.source_target_offsets)
        if sources != self.identity.sources:
            raise ValueError("compiled Join offsets must exactly cover its canonical sources")
        if any(type(offset) is not int or offset < 1 for _source, offset in self.source_target_offsets):
            raise ValueError("compiled Join source offsets must be positive integers")

    def target_offset(self, source: GraphNodeId) -> int:
        """Return the target-coordinate offset for one declared source."""

        offset = next(
            (offset for candidate, offset in self.source_target_offsets if candidate == source),
            None,
        )
        if offset is None:
            raise SnapshotMismatchError("compiled Join does not contain the arrival source")
        return offset

    def occurrence_for(self, activation: GraphActivationIdentity) -> GraphJoinOccurrenceIdentity:
        """Project one admitted source activation into its Join occurrence."""

        return GraphJoinOccurrenceIdentity(
            self.identity,
            activation.run_id,
            activation.superstep + self.target_offset(activation.node_id),
        )


@dataclass(frozen=True, slots=True)
class FrontierTransitionPlan(Generic[GraphValueT]):
    entries: tuple[GraphNodeId, ...]
    direct_targets: FrozenMap[GraphNodeId, tuple[GraphNodeId, ...]]
    conditional_targets: FrozenMap[GraphNodeId, FrozenMap[GraphRouteId, GraphNodeId]]
    joins_by_source: FrozenMap[GraphNodeId, tuple[CompiledJoin, ...]]
    materializations: FrozenMap[GraphNodeId, MaterializationPlan[GraphValueT]]
    publications: FrozenMap[GraphNodeId, FrameDescriptor[GraphValueT]]
    graph_outputs: GraphOutputBindings[GraphValueT]
    resource_order: tuple[ResourceId, ...]
    activation_gates: FrozenMap[GraphNodeId, tuple[ActivationGate, ...]]


@dataclass(frozen=True, slots=True)
class CompiledGraph(Generic[GraphValueT]):
    definition_id: GraphDefinitionId
    version: GraphDefinitionVersion
    definition_scope: DefinitionScope
    nodes: FrozenMap[GraphNodeId, GraphNode[GraphValueT]]
    nested_graphs: FrozenMap[GraphNodeId, "CompiledGraph[GraphValueT]"]
    graph_input_descriptor: FrameDescriptor[GraphValueT]
    graph_output_descriptor: FrameDescriptor[GraphValueT]
    transition: FrontierTransitionPlan[GraphValueT]
    resources: FrozenMap[ResourceId, ResourceDefinition]
    resume_input: ResumeInputBinding[GraphValueT] | None


def _compiled_graph_at_scope(
    root: CompiledGraph[GraphValueT],
    scope: DefinitionScope,
) -> CompiledGraph[GraphValueT]:
    current = root
    for segment in scope:
        try:
            current = current.nested_graphs[segment]
        except KeyError as error:
            raise SnapshotMismatchError(f"scope references unknown nested node {segment!r}") from error
    return current


def frozen_map(values: Mapping[KeyT, ValueT_co]) -> FrozenMap[KeyT, ValueT_co]:
    return FrozenMap(tuple(sorted(values.items(), key=lambda item: item[0])))


__all__ = ["_compiled_graph_at_scope"]

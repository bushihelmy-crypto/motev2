"""Compiled immutable graph plans shared by runtime and recovery."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from mote_kernel.execution.graph.definition import GraphNode
from mote_kernel.execution.graph.edge import JoinEdge
from mote_kernel.execution.graph.ports import (
    DefinitionScope,
    FrameDescriptor,
    GraphOutputBindings,
    MaterializationPlan,
    OutcomeAdmissionPlan,
    OutputDeclarations,
    PublicationPlan,
)
from mote_kernel.execution.graph.resume_input import ResumeInputBinding
from mote_kernel.execution.resource import ResourceDefinition, ResourceId
from mote_kernel.state.graph_state import (
    GraphDefinitionId,
    GraphDefinitionVersion,
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
class DataTriggerPlan:
    targets: tuple[GraphNodeId, ...]


@dataclass(frozen=True, slots=True)
class FrontierTransitionPlan(Generic[GraphValueT]):
    entries: tuple[GraphNodeId, ...]
    direct_targets: FrozenMap[GraphNodeId, tuple[GraphNodeId, ...]]
    conditional_targets: FrozenMap[GraphNodeId, FrozenMap[GraphRouteId, GraphNodeId]]
    joins_by_source: FrozenMap[GraphNodeId, tuple[JoinEdge, ...]]
    data_triggers: FrozenMap[GraphNodeId, DataTriggerPlan]
    materializations: FrozenMap[GraphNodeId, MaterializationPlan[GraphValueT]]
    outcomes: FrozenMap[GraphNodeId, OutcomeAdmissionPlan[GraphValueT]]
    publications: FrozenMap[GraphNodeId, PublicationPlan[GraphValueT]]
    graph_outputs: GraphOutputBindings[GraphValueT]
    resource_order: tuple[ResourceId, ...]


@dataclass(frozen=True, slots=True)
class RecoveryAvailabilityPlan(Generic[GraphValueT]):
    transition: FrontierTransitionPlan[GraphValueT]


@dataclass(frozen=True, slots=True)
class CompiledGraph(Generic[GraphValueT]):
    definition_id: GraphDefinitionId
    version: GraphDefinitionVersion
    definition_scope: DefinitionScope
    nodes: FrozenMap[GraphNodeId, GraphNode[GraphValueT]]
    nested_graphs: FrozenMap[GraphNodeId, "CompiledGraph[GraphValueT]"]
    graph_inputs: OutputDeclarations[GraphValueT]
    graph_input_descriptor: FrameDescriptor[GraphValueT]
    graph_output_descriptor: FrameDescriptor[GraphValueT]
    recovery: RecoveryAvailabilityPlan[GraphValueT]
    resources: FrozenMap[ResourceId, ResourceDefinition]
    resume_input: ResumeInputBinding[GraphValueT] | None

    @property
    def transition(self) -> FrontierTransitionPlan[GraphValueT]:
        return self.recovery.transition

    @property
    def entries(self) -> tuple[GraphNodeId, ...]:
        return self.transition.entries

    @property
    def materializations(self) -> FrozenMap[GraphNodeId, MaterializationPlan[GraphValueT]]:
        return self.transition.materializations

    @property
    def outcomes(self) -> FrozenMap[GraphNodeId, OutcomeAdmissionPlan[GraphValueT]]:
        return self.transition.outcomes

    @property
    def publications(self) -> FrozenMap[GraphNodeId, PublicationPlan[GraphValueT]]:
        return self.transition.publications

    @property
    def graph_outputs(self) -> GraphOutputBindings[GraphValueT]:
        return self.transition.graph_outputs

    @property
    def resource_order(self) -> tuple[ResourceId, ...]:
        return self.transition.resource_order


def frozen_map(values: Mapping[KeyT, ValueT_co]) -> FrozenMap[KeyT, ValueT_co]:
    return FrozenMap(tuple(sorted(values.items(), key=lambda item: item[0])))


__all__: list[str] = []

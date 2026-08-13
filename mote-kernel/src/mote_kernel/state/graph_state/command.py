"""Typed transition inputs for recoverable graph-run state."""

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.state.graph_state.frontier_model import (
    GraphFailure,
    GraphInterruptPayload,
    GraphNodeInputBinding,
    GraphNodeInterruptIdentity,
    GraphResumeInputCodec,
    GraphSkipReason,
    OverrideGraphNodeInput,
)
from mote_kernel.state.graph_state.identity import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    GraphExecutionAttemptId,
    GraphInterruptId,
    GraphNodeId,
    GraphRunId,
)
from mote_kernel.state.graph_state.model import (
    GraphAbortReason,
    GraphExecutionToken,
    GraphJoinProgress,
    ParentGraphActivation,
)
from mote_kernel.state.graph_state.resource_model import ResourceSnapshot
from mote_kernel.state.graph_state.routing import GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class SucceededGraphNodeOutcome:
    node_id: GraphNodeId
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class FailedGraphNodeOutcome:
    node_id: GraphNodeId
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class InterruptedGraphNodeOutcome:
    node_id: GraphNodeId
    identity: GraphNodeInterruptIdentity
    request_payload: GraphInterruptPayload


GraphNodeOutcome: TypeAlias = SucceededGraphNodeOutcome | FailedGraphNodeOutcome | InterruptedGraphNodeOutcome


@dataclass(frozen=True, slots=True)
class AdvanceGraphFrontier:
    node_ids: tuple[GraphNodeId, ...]
    join_progress: tuple[GraphJoinProgress, ...]


@dataclass(frozen=True, slots=True)
class CompleteGraphFrontier:
    pass


GraphFrontierResolution: TypeAlias = AdvanceGraphFrontier | CompleteGraphFrontier


@dataclass(frozen=True, slots=True)
class ResumeFailedNode:
    node_id: GraphNodeId
    input: GraphNodeInputBinding


@dataclass(frozen=True, slots=True)
class SkipFailedNode:
    node_id: GraphNodeId
    reason: GraphSkipReason
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class ResumeInterruptedNode:
    node_id: GraphNodeId
    interrupt_id: GraphInterruptId
    input: OverrideGraphNodeInput


GraphNodeResumeAction: TypeAlias = ResumeFailedNode | SkipFailedNode | ResumeInterruptedNode


@dataclass(frozen=True, slots=True)
class StartGraphRun:
    run_id: GraphRunId
    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    node_ids: tuple[GraphNodeId, ...]
    parent: ParentGraphActivation | None = None
    resume_input_codec: GraphResumeInputCodec | None = None


@dataclass(frozen=True, slots=True)
class ClaimGraphExecution:
    expected_revision: int
    attempt_id: GraphExecutionAttemptId
    node_ids: tuple[GraphNodeId, ...]


@dataclass(frozen=True, slots=True)
class FenceGraphExecution:
    expected_revision: int
    execution: GraphExecutionToken


@dataclass(frozen=True, slots=True)
class SettleGraphExecution:
    expected_revision: int
    execution: GraphExecutionToken
    outcomes: tuple[GraphNodeOutcome, ...]
    resolution: GraphFrontierResolution | None


@dataclass(frozen=True, slots=True)
class ResumeGraphNodes:
    expected_revision: int
    actions: tuple[GraphNodeResumeAction, ...]
    resolution: GraphFrontierResolution | None


@dataclass(frozen=True, slots=True)
class AbortGraphRun:
    expected_revision: int
    reason: GraphAbortReason


@dataclass(frozen=True, slots=True)
class UpdateGraphResources:
    expected_revision: int
    resources: ResourceSnapshot


GraphRunCommand: TypeAlias = (
    StartGraphRun
    | ClaimGraphExecution
    | FenceGraphExecution
    | SettleGraphExecution
    | ResumeGraphNodes
    | AbortGraphRun
    | UpdateGraphResources
)


__all__ = [
    "AbortGraphRun",
    "AdvanceGraphFrontier",
    "ClaimGraphExecution",
    "CompleteGraphFrontier",
    "FailedGraphNodeOutcome",
    "FenceGraphExecution",
    "GraphFrontierResolution",
    "GraphNodeOutcome",
    "GraphNodeResumeAction",
    "GraphRunCommand",
    "InterruptedGraphNodeOutcome",
    "ResumeFailedNode",
    "ResumeGraphNodes",
    "ResumeInterruptedNode",
    "SettleGraphExecution",
    "SkipFailedNode",
    "StartGraphRun",
    "SucceededGraphNodeOutcome",
    "UpdateGraphResources",
]

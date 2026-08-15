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
    expected_revision: int
    node_ids: tuple[GraphNodeId, ...]
    join_progress: tuple[GraphJoinProgress, ...]


@dataclass(frozen=True, slots=True)
class CompleteGraphFrontier:
    expected_revision: int


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
    resources: ResourceSnapshot | None


@dataclass(frozen=True, slots=True)
class FenceGraphExecution:
    expected_revision: int
    execution: GraphExecutionToken


@dataclass(frozen=True, slots=True)
class SettleGraphNode:
    expected_revision: int
    execution: GraphExecutionToken
    outcome: GraphNodeOutcome


@dataclass(frozen=True, slots=True)
class ResumeGraphNodes:
    expected_revision: int
    actions: tuple[GraphNodeResumeAction, ...]


@dataclass(frozen=True, slots=True)
class AbortGraphRun:
    expected_revision: int
    reason: GraphAbortReason


GraphRunCommand: TypeAlias = (
    StartGraphRun
    | ClaimGraphExecution
    | FenceGraphExecution
    | SettleGraphNode
    | ResumeGraphNodes
    | AdvanceGraphFrontier
    | CompleteGraphFrontier
    | AbortGraphRun
)


__all__ = [
    "AbortGraphRun",
    "AdvanceGraphFrontier",
    "ClaimGraphExecution",
    "CompleteGraphFrontier",
    "FailedGraphNodeOutcome",
    "FenceGraphExecution",
    "GraphNodeOutcome",
    "GraphNodeResumeAction",
    "GraphRunCommand",
    "InterruptedGraphNodeOutcome",
    "ResumeFailedNode",
    "ResumeGraphNodes",
    "ResumeInterruptedNode",
    "SettleGraphNode",
    "SkipFailedNode",
    "StartGraphRun",
    "SucceededGraphNodeOutcome",
]

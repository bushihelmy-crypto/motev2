"""Backend-independent commit evidence and cold-recovery materialization."""

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Generic, Protocol, TypeVar

from mote_kernel.config import Config, ConfigContractError, ConfigSnapshotKey, require_config
from mote_kernel.execution.commit import GraphCommitKey, GraphTransition
from mote_kernel.execution.engine.admission import project_graph_outputs
from mote_kernel.execution.engine.routing import graph_outputs_available
from mote_kernel.execution.engine.snapshot_guard import require_scoped_snapshot_matches_graph
from mote_kernel.execution.errors import (
    GraphValidationError,
    GraphValueAdmissionError,
    GraphValuePublicationError,
    SnapshotMismatchError,
)
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.graph.ports import FrameDescriptorIdentity, canonical_nominal_type
from mote_kernel.execution.graph.topology import CompiledGraph, _compiled_graph_at_scope
from mote_kernel.execution.graph.values import (
    GraphInputFrame,
    NodeOutputFrame,
    _GraphValues,
    _make_graph_input_frame,
    _make_graph_values,
    _make_node_output_frame,
    admit_exact,
)
from mote_kernel.execution.identity import ScopeRunCoordinate, StableActivation
from mote_kernel.execution.invocation import lineage_states, validate_context
from mote_kernel.execution.run_context import (
    AdmittedGraphInput,
    ChildBoundaryAvailabilityCoordinate,
    ConfirmedChildBoundary,
    ConfirmedPublication,
    ExecutionPublicationProvenance,
    GraphInputAvailabilityCoordinate,
    PublicationAvailabilityCoordinate,
    ScopedFrameIndex,
    ScopedRunEvidence,
    ScopedStateBinding,
    UncreatedGraphRun,
    require_publication_confirmation,
)
from mote_kernel.state.graph_state import (
    GraphConfigCursor,
    GraphNodeId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    validate_graph_run_state,
)
from mote_kernel.state.graph_state.identity import is_canonical_identity

GraphValueT = TypeVar("GraphValueT")


def _frame_digest(
    codec_id: str,
    codec_version: int,
    payload: bytes,
    config_cursor: GraphConfigCursor | None,
) -> str:
    if not is_canonical_identity(codec_id) or type(codec_version) is not int or codec_version < 1:
        raise SnapshotMismatchError("persistent frame codec identity and version must be canonical")
    if type(payload) is not bytes:
        raise SnapshotMismatchError("persistent frame integrity check failed")
    cursor = None
    if config_cursor is not None:
        try:
            cursor = GraphConfigCursor.admit(config_cursor)
        except (TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent frame Config cursor is malformed") from error
        if cursor.digest is None:
            raise SnapshotMismatchError("persistent frame Config requires an exact snapshot digest")
    metadata = json.dumps(
        (
            codec_id,
            codec_version,
            (cursor.definition_id, cursor.definition_version, cursor.revision, cursor.digest)
            if cursor is not None
            else None,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = sha256(b"mote.graph-frame\x00" + metadata + b"\x00")
    digest.update(payload)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class EncodedFrame:
    codec_id: str
    codec_version: int
    payload: bytes
    frame_digest: str
    config_cursor: GraphConfigCursor | None = None

    def __post_init__(self) -> None:
        expected = _frame_digest(self.codec_id, self.codec_version, self.payload, self.config_cursor)
        if type(self.frame_digest) is not str or self.frame_digest != expected:
            raise SnapshotMismatchError("persistent frame integrity check failed")

    @classmethod
    def capture(
        cls,
        codec_id: str,
        codec_version: int,
        payload: bytes,
        config_cursor: GraphConfigCursor | None = None,
    ) -> "EncodedFrame":
        return cls(
            codec_id,
            codec_version,
            payload,
            _frame_digest(codec_id, codec_version, payload, config_cursor),
            config_cursor,
        )


@dataclass(frozen=True, slots=True)
class PersistedGraphInput(Generic[GraphValueT]):
    coordinate: GraphInputAvailabilityCoordinate[GraphValueT]
    frame: EncodedFrame

    def __post_init__(self) -> None:
        coordinate = self.coordinate
        if (
            type(coordinate) is not GraphInputAvailabilityCoordinate
            or type(coordinate.scope_run) is not ScopeRunCoordinate
            or type(coordinate.descriptor) is not FrameDescriptorIdentity
            or type(self.frame) is not EncodedFrame
        ):
            raise SnapshotMismatchError("persistent graph input has malformed typed evidence")
        replace(coordinate.scope_run)
        try:
            replace(coordinate.descriptor)
        except GraphValidationError as error:
            raise SnapshotMismatchError("persistent graph input descriptor is malformed") from error
        replace(self.frame)


@dataclass(frozen=True, slots=True)
class PersistedPublication(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: EncodedFrame
    acknowledged_revision: int
    provenance: ExecutionPublicationProvenance

    def __post_init__(self) -> None:
        coordinate = self.coordinate
        if (
            type(coordinate) is not PublicationAvailabilityCoordinate
            or type(coordinate.activation) is not StableActivation
            or type(coordinate.descriptor) is not FrameDescriptorIdentity
            or type(self.frame) is not EncodedFrame
        ):
            raise SnapshotMismatchError("persistent publication has malformed typed evidence")
        replace(coordinate.activation)
        replace(coordinate.activation.scope_run)
        try:
            replace(coordinate.descriptor)
        except GraphValidationError as error:
            raise SnapshotMismatchError("persistent publication descriptor is malformed") from error
        replace(self.frame)
        require_publication_confirmation(self.acknowledged_revision, self.provenance)


@dataclass(frozen=True, slots=True)
class GraphPersistenceWriteSet(Generic[GraphValueT]):
    commit_key: GraphCommitKey
    graph_inputs: tuple[PersistedGraphInput[GraphValueT], ...]
    publications: tuple[PersistedPublication[GraphValueT], ...]

    def __post_init__(self) -> None:
        if type(self.commit_key) is not GraphCommitKey:
            raise SnapshotMismatchError("persistent write set requires a typed commit key")
        replace(self.commit_key)
        if type(self.graph_inputs) is not tuple or any(
            type(item) is not PersistedGraphInput for item in self.graph_inputs
        ):
            raise SnapshotMismatchError("persistent graph input writes must be typed immutable records")
        if type(self.publications) is not tuple or any(
            type(item) is not PersistedPublication for item in self.publications
        ):
            raise SnapshotMismatchError("persistent publication writes must be typed immutable records")
        for graph_input in self.graph_inputs:
            replace(graph_input)
        for publication in self.publications:
            replace(publication)


@dataclass(frozen=True, slots=True)
class GraphPersistenceCommit(Generic[GraphValueT]):
    """An immutable CAS request; all equality-participating values are durable facts."""

    scope: tuple[GraphNodeId, ...]
    expected_revision: int | None
    candidate_state: GraphRunState
    writes: GraphPersistenceWriteSet[GraphValueT]

    def __post_init__(self) -> None:
        if self.expected_revision is not None and (
            type(self.expected_revision) is not int or self.expected_revision < 0
        ):
            raise SnapshotMismatchError("persistent commit expected revision must be absent or a non-negative integer")
        try:
            validate_graph_run_state(self.candidate_state)
        except GraphStateTransitionError as error:
            raise SnapshotMismatchError("persistent commit candidate is malformed") from error
        ScopeRunCoordinate(self.scope, self.candidate_state.run_id)
        if type(self.writes) is not GraphPersistenceWriteSet:
            raise SnapshotMismatchError("persistent commit requires a typed complete write set")
        replace(self.writes)


class GraphPersistenceWriter(Protocol[GraphValueT]):
    async def __call__(
        self,
        request: GraphPersistenceCommit[GraphValueT],
        /,
    ) -> GraphPersistenceCommit[GraphValueT]: ...


def _encode_frame(
    frame: GraphInputFrame[GraphValueT] | NodeOutputFrame[GraphValueT],
    codec: FrameCodec[GraphValueT],
) -> EncodedFrame:
    values = _make_graph_values(**{entry.name: entry.value for entry in frame.entries})
    payload = codec.encode(values)
    decoded = codec.decode(payload)
    if decoded.activation_config is not None or decoded.keys() != values.keys():
        raise GraphValueAdmissionError("persistent codec must preserve frame names without encoding Config")
    for entry in frame.entries:
        admit_exact(decoded[entry.name], canonical_nominal_type(type(entry.value)), kind="persistent round-trip value")
    if codec.encode(decoded) != payload or codec.encode(values) != payload:
        raise GraphValueAdmissionError("persistent codec must have a deterministic canonical round trip")
    config = frame.activation_config
    return EncodedFrame.capture(
        codec.codec_id,
        codec.version,
        payload,
        require_config(config).config_cursor if config is not None else None,
    )


@dataclass(frozen=True, slots=True)
class DurableGraphCommit(Generic[GraphValueT]):
    """Adapt one exact persistent confirmation to the existing Graph commit port."""

    codec: FrameCodec[GraphValueT]
    writer: GraphPersistenceWriter[GraphValueT]

    def __post_init__(self) -> None:
        if type(self.codec) is not FrameCodec or not callable(self.writer):
            raise GraphValidationError("durable graph commit requires a typed codec and writer")
        self.codec.validate()

    async def __call__(self, transition: GraphTransition[GraphValueT], /) -> GraphRunState:
        writes = transition.writes
        request = GraphPersistenceCommit(
            tuple(GraphNodeId(segment) for segment in transition.scope),
            transition.previous_state.revision if transition.previous_state is not None else None,
            transition.candidate_state,
            GraphPersistenceWriteSet(
                writes.commit_key,
                tuple(
                    PersistedGraphInput(evidence.coordinate, _encode_frame(evidence.frame, self.codec))
                    for evidence in writes.graph_inputs
                ),
                tuple(
                    PersistedPublication(
                        evidence.coordinate,
                        _encode_frame(evidence.frame, self.codec),
                        writes.commit_key.revision,
                        evidence.provenance,
                    )
                    for evidence in writes.publications
                ),
            ),
        )
        confirmed = await self.writer(request)
        if type(confirmed) is not GraphPersistenceCommit:
            raise SnapshotMismatchError("persistence must confirm the exact state and complete value write set")
        confirmed = replace(confirmed)
        if confirmed != request:
            raise SnapshotMismatchError("persistence must confirm the exact state and complete value write set")
        return transition.candidate_state


@dataclass(frozen=True, slots=True)
class GraphCheckpoint(Generic[GraphValueT]):
    """One consistent persistence read, not another runtime state model."""

    root_state: GraphRunState
    child_runs: tuple[ScopedRunEvidence, ...]
    graph_inputs: tuple[PersistedGraphInput[GraphValueT], ...]
    publications: tuple[PersistedPublication[GraphValueT], ...]

    def __post_init__(self) -> None:
        if type(self.root_state) is not GraphRunState:
            raise SnapshotMismatchError("checkpoint requires an authoritative root state")
        if type(self.child_runs) is not tuple or any(
            type(item) not in (ScopedStateBinding, UncreatedGraphRun)
            or type(item.scope_run) is not ScopeRunCoordinate
            or (isinstance(item, ScopedStateBinding) and type(item.state) is not GraphRunState)
            for item in self.child_runs
        ):
            raise SnapshotMismatchError("checkpoint child states must be typed immutable bindings")
        if type(self.graph_inputs) is not tuple or any(
            type(item) is not PersistedGraphInput for item in self.graph_inputs
        ):
            raise SnapshotMismatchError("checkpoint graph inputs must be typed immutable records")
        if type(self.publications) is not tuple or any(
            type(item) is not PersistedPublication for item in self.publications
        ):
            raise SnapshotMismatchError("checkpoint publications must be typed immutable records")
        for child in self.child_runs:
            replace(child.scope_run)
            if isinstance(child, UncreatedGraphRun):
                replace(child)
        for graph_input in self.graph_inputs:
            replace(graph_input)
        for publication in self.publications:
            replace(publication)


@dataclass(frozen=True, slots=True)
class GraphRecovery(Generic[GraphValueT]):
    """Agent-supplied read material plus exact, already resolved Config capabilities."""

    checkpoint: GraphCheckpoint[GraphValueT]
    commit: DurableGraphCommit[GraphValueT]
    configs: tuple[Config, ...] = ()

    def __post_init__(self) -> None:
        if type(self.checkpoint) is not GraphCheckpoint:
            raise SnapshotMismatchError("graph recovery requires a typed checkpoint")
        replace(self.checkpoint)
        if type(self.commit) is not DurableGraphCommit:
            raise GraphValidationError("durable recovery requires its durable commit capability")
        replace(self.commit)
        if type(self.configs) is not tuple:
            raise SnapshotMismatchError("recovery Config capabilities must be an immutable tuple")


def _resolved_config(cursor: GraphConfigCursor, configs: dict[ConfigSnapshotKey, Config]) -> Config:
    key = ConfigSnapshotKey(cursor.definition_id, cursor.definition_version, cursor.revision)
    config = configs.get(key)
    if config is None or config.config_cursor != cursor:
        raise SnapshotMismatchError("checkpoint requires its exact resolved Config snapshot")
    return config


def _decode_frame(
    frame: EncodedFrame,
    codec: FrameCodec[GraphValueT],
    configs: dict[ConfigSnapshotKey, Config],
) -> tuple[_GraphValues[GraphValueT], Config | None]:
    if frame.codec_id != codec.codec_id or frame.codec_version != codec.version:
        raise SnapshotMismatchError("persistent frame codec identity or version does not match")
    values = codec.decode(frame.payload)
    if values.activation_config is not None or codec.encode(values) != frame.payload:
        raise SnapshotMismatchError("persistent frame is not a canonical Config-free value encoding")
    config = _resolved_config(frame.config_cursor, configs) if frame.config_cursor is not None else None
    return values, config


def restore_checkpoint(
    graph: CompiledGraph[GraphValueT],
    recovery: GraphRecovery[GraphValueT],
) -> ScopedFrameIndex[GraphValueT]:
    """Materialize durable evidence before the existing fence/resume admission."""

    checkpoint = recovery.checkpoint
    lineage = lineage_states(checkpoint.root_state, checkpoint.child_runs)
    configs: dict[ConfigSnapshotKey, Config] = {}
    try:
        for config in recovery.configs:
            key = require_config(config).snapshot.key
            if key in configs:
                raise SnapshotMismatchError("recovery repeats one immutable Config revision")
            configs[key] = config
    except ConfigContractError as error:
        raise SnapshotMismatchError("recovery Config capability is malformed") from error
    for binding in lineage.bindings:
        scoped_graph = _compiled_graph_at_scope(graph, binding.scope_run.scope)
        require_scoped_snapshot_matches_graph(scoped_graph, binding.state, binding.scope_run)
        if binding.state.config_digest is not None:
            _resolved_config(binding.state.config_cursor, configs)
    frames: ScopedFrameIndex[GraphValueT] = ScopedFrameIndex()
    try:
        for graph_input in checkpoint.graph_inputs:
            coordinate = graph_input.coordinate
            scoped_graph = _compiled_graph_at_scope(graph, coordinate.scope_run.scope)
            values, config = _decode_frame(graph_input.frame, recovery.commit.codec, configs)
            frame = _make_graph_input_frame(values, scoped_graph.graph_input_descriptor.declarations, config)
            frames = frames.add_graph_input(AdmittedGraphInput(coordinate, frame))
        for publication in checkpoint.publications:
            publication_coordinate = publication.coordinate
            scoped_graph = _compiled_graph_at_scope(graph, publication_coordinate.activation.scope_run.scope)
            descriptor = scoped_graph.transition.publications.get(publication_coordinate.activation.node_id)
            if descriptor is None:
                raise SnapshotMismatchError("persistent publication references an unknown node")
            values, config = _decode_frame(publication.frame, recovery.commit.codec, configs)
            output = _make_node_output_frame(values, descriptor.declarations, config)
            frames = frames.add_publication(
                ConfirmedPublication(
                    publication_coordinate,
                    output,
                    publication.acknowledged_revision,
                    publication.provenance,
                )
            )
        validate_context(graph, lineage, frames, recovered=True)
        for binding in sorted(lineage.bindings, key=lambda item: len(item.scope_run.scope), reverse=True):
            if binding.state.status is not GraphRunStatus.COMPLETED:
                continue
            scoped_graph = _compiled_graph_at_scope(graph, binding.scope_run.scope)
            if not graph_outputs_available(scoped_graph, binding.scope_run, binding.state.superstep, frames):
                raise SnapshotMismatchError("checkpoint is missing required completed graph output evidence")
            if not binding.scope_run.scope:
                continue
            boundary: ChildBoundaryAvailabilityCoordinate[GraphValueT] = ChildBoundaryAvailabilityCoordinate(
                binding.scope_run,
                scoped_graph.graph_output_descriptor.identity,
            )
            frames = frames.add_child_boundary(
                ConfirmedChildBoundary(
                    boundary,
                    project_graph_outputs(scoped_graph, binding.scope_run, binding.state.superstep, frames),
                )
            )
        validate_context(graph, lineage, frames, recovered=False)
    except (GraphValueAdmissionError, GraphValuePublicationError) as error:
        raise SnapshotMismatchError("persistent value evidence cannot be admitted by the compiled graph") from error
    return frames


__all__: list[str] = []

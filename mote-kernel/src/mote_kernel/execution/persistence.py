"""Backend-independent commit evidence and cold-recovery materialization."""

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Generic, Protocol, TypeVar

from mote_kernel.config import Config, ConfigContractError, ConfigSnapshotKey, require_config
from mote_kernel.execution.commit import GraphCommitKey, GraphTransition, bind_transition_evidence
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
from mote_kernel.session import (
    AgentSessionCarrier,
    AgentSessionCodecCarrier,
    AgentSessionContractError,
    EncodedAgentSession,
    admit_session_carrier,
    admit_session_codec_carrier,
    encode_session_carrier,
)
from mote_kernel.state.graph_state import (
    ActivationReference,
    GraphActivationIdentity,
    GraphConfigCursor,
    GraphEvidenceCommitment,
    GraphNodeId,
    GraphRouteId,
    GraphRunId,
    GraphRunState,
    GraphRunStatus,
    GraphStateTransitionError,
    validate_graph_run_state,
)
from mote_kernel.state.graph_state.identity import is_canonical_identity

GraphValueT = TypeVar("GraphValueT")

_DescriptorCommitmentParts = tuple[str, int, int, int]
_ConfigCommitmentParts = tuple[str, int, int, str] | None
_GraphInputCommitmentMetadata = tuple[
    GraphRunId,
    int,
    tuple[GraphNodeId, ...],
    GraphRunId,
    _DescriptorCommitmentParts,
    str,
    int,
    _ConfigCommitmentParts,
]
_PublicationCommitmentMetadata = tuple[
    GraphRunId,
    int,
    tuple[GraphNodeId, ...],
    GraphRunId,
    int,
    GraphNodeId,
    _DescriptorCommitmentParts,
    int,
    str,
    str,
    int,
    _ConfigCommitmentParts,
    GraphRouteId | None,
]
_SessionCommitmentMetadata = tuple[
    tuple[GraphNodeId, ...],
    GraphRunId,
    int,
    str,
    int,
    _ConfigCommitmentParts,
]


@dataclass(frozen=True, slots=True)
class EncodedFrame:
    codec_id: str
    codec_version: int
    payload: bytes
    config_cursor: GraphConfigCursor | None = None

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.codec_id) or type(self.codec_version) is not int or self.codec_version < 1:
            raise SnapshotMismatchError("persistent frame codec identity and version must be canonical")
        if type(self.payload) is not bytes:
            raise SnapshotMismatchError("persistent frame payload must be exact immutable bytes")
        if self.config_cursor is not None:
            try:
                cursor = GraphConfigCursor.admit(self.config_cursor)
            except (TypeError, ValueError) as error:
                raise SnapshotMismatchError("persistent frame Config cursor is malformed") from error
            if cursor.digest is None:
                raise SnapshotMismatchError("persistent frame Config requires an exact snapshot digest")

    @classmethod
    def admit(cls, frame: "EncodedFrame", /) -> "EncodedFrame":
        if type(frame) is not cls:
            raise SnapshotMismatchError("persistent frame must be an exact encoded envelope")
        try:
            return cls(frame.codec_id, frame.codec_version, frame.payload, frame.config_cursor)
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent frame is malformed") from error


def _descriptor_parts(descriptor: FrameDescriptorIdentity) -> _DescriptorCommitmentParts:
    return (
        descriptor.definition_id,
        descriptor.definition_version,
        descriptor.frame_kind.value,
        descriptor.owner_ordinal,
    )


def _config_parts(cursor: GraphConfigCursor | None) -> _ConfigCommitmentParts:
    if cursor is None:
        return None
    return (cursor.definition_id, cursor.definition_version, cursor.revision, cursor.digest or "")


def _commitment(
    domain: bytes,
    metadata: _GraphInputCommitmentMetadata | _PublicationCommitmentMetadata | _SessionCommitmentMetadata,
    payload: bytes,
) -> GraphEvidenceCommitment:
    encoded = json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    digest = sha256(domain + b"\x00" + encoded + b"\x00")
    digest.update(payload)
    return GraphEvidenceCommitment(digest.digest())


def _session_commitment(
    scope: tuple[GraphNodeId, ...],
    commit_key: GraphCommitKey,
    session: EncodedAgentSession,
) -> GraphEvidenceCommitment:
    return _commitment(
        b"mote.graph-session-evidence.v1",
        (
            scope,
            commit_key.run_id,
            commit_key.revision,
            session.codec_id,
            session.codec_version,
            _config_parts(session.config_cursor),
        ),
        session.payload,
    )


def _graph_input_commitment(
    coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
    frame: EncodedFrame,
    birth: GraphCommitKey,
) -> GraphEvidenceCommitment:
    return _commitment(
        b"mote.graph-input-evidence.v1",
        (
            birth.run_id,
            birth.revision,
            coordinate.scope_run.scope,
            coordinate.scope_run.graph_run_id,
            _descriptor_parts(coordinate.descriptor),
            frame.codec_id,
            frame.codec_version,
            _config_parts(frame.config_cursor),
        ),
        frame.payload,
    )


def _publication_commitment(
    coordinate: PublicationAvailabilityCoordinate[GraphValueT],
    frame: EncodedFrame,
    birth: GraphCommitKey,
    provenance: ExecutionPublicationProvenance,
    settlement_reference: ActivationReference,
) -> GraphEvidenceCommitment:
    token = require_publication_confirmation(birth.revision, provenance)
    activation = coordinate.activation
    if settlement_reference.activation != GraphActivationIdentity(
        activation.scope_run.graph_run_id,
        activation.superstep,
        activation.node_id,
    ):
        raise SnapshotMismatchError("publication settlement reference does not match its coordinate")
    return _commitment(
        b"mote.graph-publication-evidence.v1",
        (
            birth.run_id,
            birth.revision,
            activation.scope_run.scope,
            activation.scope_run.graph_run_id,
            activation.superstep,
            activation.node_id,
            _descriptor_parts(coordinate.descriptor),
            token.generation,
            token.attempt_id,
            frame.codec_id,
            frame.codec_version,
            _config_parts(frame.config_cursor),
            settlement_reference.route,
        ),
        frame.payload,
    )


def _admit_graph_input_coordinate(
    coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
) -> GraphInputAvailabilityCoordinate[GraphValueT]:
    if type(coordinate) is not GraphInputAvailabilityCoordinate:
        raise SnapshotMismatchError("persistent graph input coordinate is malformed")
    try:
        scope_run = ScopeRunCoordinate(tuple(coordinate.scope_run.scope), coordinate.scope_run.graph_run_id)
        descriptor = FrameDescriptorIdentity(
            coordinate.descriptor.definition_id,
            coordinate.descriptor.definition_version,
            coordinate.descriptor.frame_kind,
            coordinate.descriptor.owner_ordinal,
        )
        return GraphInputAvailabilityCoordinate(scope_run, descriptor)
    except (AttributeError, GraphValidationError, SnapshotMismatchError, TypeError, ValueError) as error:
        raise SnapshotMismatchError("persistent graph input coordinate is malformed") from error


def _admit_publication_coordinate(
    coordinate: PublicationAvailabilityCoordinate[GraphValueT],
) -> PublicationAvailabilityCoordinate[GraphValueT]:
    if type(coordinate) is not PublicationAvailabilityCoordinate:
        raise SnapshotMismatchError("persistent publication coordinate is malformed")
    try:
        activation = coordinate.activation
        scope_run = ScopeRunCoordinate(tuple(activation.scope_run.scope), activation.scope_run.graph_run_id)
        stable = StableActivation(scope_run, activation.superstep, activation.node_id)
        descriptor = FrameDescriptorIdentity(
            coordinate.descriptor.definition_id,
            coordinate.descriptor.definition_version,
            coordinate.descriptor.frame_kind,
            coordinate.descriptor.owner_ordinal,
        )
        return PublicationAvailabilityCoordinate(stable, descriptor)
    except (AttributeError, GraphValidationError, SnapshotMismatchError, TypeError, ValueError) as error:
        raise SnapshotMismatchError("persistent publication coordinate is malformed") from error


def _admit_commit_key(key: GraphCommitKey) -> GraphCommitKey:
    if type(key) is not GraphCommitKey:
        raise SnapshotMismatchError("persistent evidence requires an exact commit key")
    try:
        return GraphCommitKey(key.run_id, key.revision)
    except (AttributeError, TypeError, ValueError) as error:
        raise SnapshotMismatchError("persistent evidence commit key is malformed") from error


@dataclass(frozen=True, slots=True)
class GraphSessionReceipt:
    """Evidence that one encoded Session was committed with one scoped state."""

    scope: tuple[GraphNodeId, ...]
    commit_key: GraphCommitKey
    evidence: GraphEvidenceCommitment

    def __post_init__(self) -> None:
        if type(self.scope) is not tuple:
            raise SnapshotMismatchError("Session receipt scope must be a typed immutable tuple")
        try:
            key = _admit_commit_key(self.commit_key)
            ScopeRunCoordinate(tuple(self.scope), key.run_id)
            GraphEvidenceCommitment.admit(self.evidence)
        except (AttributeError, SnapshotMismatchError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("Session receipt is malformed") from error

    @classmethod
    def for_commit(
        cls,
        scope: tuple[GraphNodeId, ...],
        commit_key: GraphCommitKey,
        session: EncodedAgentSession,
    ) -> "GraphSessionReceipt":
        try:
            session = EncodedAgentSession.admit(session)
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("Session receipt requires a valid encoded Session") from error
        return cls(scope, commit_key, _session_commitment(scope, commit_key, session))

    def admit(self) -> "GraphSessionReceipt":
        if type(self) is not GraphSessionReceipt:
            raise SnapshotMismatchError("Session receipt must be an exact typed record")
        return GraphSessionReceipt(self.scope, self.commit_key, self.evidence)


def _validate_persistent_config_session(
    candidate_state: GraphRunState,
    expected_config_cursor: GraphConfigCursor | None,
    agent_session: EncodedAgentSession | None,
) -> None:
    """Admit the one predecessor Config fact and its Session successor rule."""

    candidate_cursor = candidate_state.config_cursor
    if expected_config_cursor is not None:
        try:
            expected_config_cursor = GraphConfigCursor.admit(expected_config_cursor)
        except (TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent commit predecessor Config cursor is malformed") from error
    if candidate_state.revision == 0:
        if expected_config_cursor is not None:
            raise SnapshotMismatchError("durable StartGraphRun cannot carry a predecessor Config cursor")
        if agent_session is not None:
            active_cursor = candidate_cursor if candidate_state.config_digest is not None else None
            if agent_session.config_cursor != active_cursor:
                raise SnapshotMismatchError("persistent commit AgentSession does not match candidate state Config")
        return
    if expected_config_cursor is None:
        raise SnapshotMismatchError("persistent commit requires its predecessor Config cursor")
    try:
        transitioned_cursor = expected_config_cursor.transition_to(candidate_cursor)
    except (AttributeError, TypeError, ValueError) as error:
        raise SnapshotMismatchError("persistent commit Config cursor does not name its predecessor") from error
    historical_digest_fill = (
        expected_config_cursor.definition_id == candidate_cursor.definition_id
        and expected_config_cursor.definition_version == candidate_cursor.definition_version
        and expected_config_cursor.revision == candidate_cursor.revision
        and expected_config_cursor.digest is None
        and candidate_cursor.digest is not None
    )
    if transitioned_cursor != candidate_cursor and not historical_digest_fill:
        raise SnapshotMismatchError("persistent commit Config cursor does not name its predecessor")
    if agent_session is None or candidate_cursor == expected_config_cursor or historical_digest_fill:
        return
    session_cursor = agent_session.config_cursor
    if session_cursor == candidate_cursor:
        return
    if (
        session_cursor is not None
        and session_cursor.definition_id == candidate_cursor.definition_id
        and session_cursor.definition_version == candidate_cursor.definition_version
        and session_cursor.revision > candidate_cursor.revision
    ):
        return
    raise SnapshotMismatchError("persistent commit AgentSession must carry the candidate Config successor")


@dataclass(frozen=True, slots=True)
class PersistedGraphInput(Generic[GraphValueT]):
    coordinate: GraphInputAvailabilityCoordinate[GraphValueT]
    frame: EncodedFrame
    birth: GraphCommitKey
    evidence: GraphEvidenceCommitment

    def __post_init__(self) -> None:
        coordinate = _admit_graph_input_coordinate(self.coordinate)
        frame = EncodedFrame.admit(self.frame)
        birth = _admit_commit_key(self.birth)
        try:
            evidence = GraphEvidenceCommitment.admit(self.evidence)
        except ValueError as error:
            raise SnapshotMismatchError("persistent graph input commitment is malformed") from error
        if birth.run_id != coordinate.scope_run.graph_run_id or birth.revision != 0:
            raise SnapshotMismatchError("persistent graph input birth commit is inconsistent")
        if evidence != _graph_input_commitment(coordinate, frame, birth):
            raise SnapshotMismatchError("persistent graph input commitment does not match its complete evidence")

    def admit(self) -> "PersistedGraphInput[GraphValueT]":
        if type(self) is not PersistedGraphInput:
            raise SnapshotMismatchError("persistent graph input must be an exact typed record")
        try:
            return PersistedGraphInput(self.coordinate, self.frame, self.birth, self.evidence)
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent graph input is malformed") from error


def _capture_graph_input(
    coordinate: GraphInputAvailabilityCoordinate[GraphValueT],
    frame: EncodedFrame,
    birth: GraphCommitKey,
) -> PersistedGraphInput[GraphValueT]:
    return PersistedGraphInput(coordinate, frame, birth, _graph_input_commitment(coordinate, frame, birth))


@dataclass(frozen=True, slots=True)
class PersistedPublication(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: EncodedFrame
    birth: GraphCommitKey
    provenance: ExecutionPublicationProvenance
    settlement_reference: ActivationReference
    evidence: GraphEvidenceCommitment

    def __post_init__(self) -> None:
        coordinate = _admit_publication_coordinate(self.coordinate)
        frame = EncodedFrame.admit(self.frame)
        birth = _admit_commit_key(self.birth)
        try:
            provenance = ExecutionPublicationProvenance.admit(self.provenance)
            settlement_reference = ActivationReference(
                GraphActivationIdentity(
                    self.settlement_reference.activation.run_id,
                    self.settlement_reference.activation.superstep,
                    self.settlement_reference.activation.node_id,
                ),
                self.settlement_reference.route,
            )
            evidence = GraphEvidenceCommitment.admit(self.evidence)
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent publication confirmation is malformed") from error
        if birth.run_id != coordinate.activation.scope_run.graph_run_id or birth.revision < 1:
            raise SnapshotMismatchError("persistent publication birth commit is inconsistent")
        if evidence != _publication_commitment(coordinate, frame, birth, provenance, settlement_reference):
            raise SnapshotMismatchError("persistent publication commitment does not match its complete evidence")

    def admit(self) -> "PersistedPublication[GraphValueT]":
        if type(self) is not PersistedPublication:
            raise SnapshotMismatchError("persistent publication must be an exact typed record")
        try:
            return PersistedPublication(
                self.coordinate,
                self.frame,
                self.birth,
                self.provenance,
                self.settlement_reference,
                self.evidence,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent publication is malformed") from error


def _capture_publication(
    coordinate: PublicationAvailabilityCoordinate[GraphValueT],
    frame: EncodedFrame,
    birth: GraphCommitKey,
    provenance: ExecutionPublicationProvenance,
    settlement_route: GraphRouteId | None,
) -> PersistedPublication[GraphValueT]:
    activation = coordinate.activation
    settlement_reference = ActivationReference(
        GraphActivationIdentity(
            activation.scope_run.graph_run_id,
            activation.superstep,
            activation.node_id,
        ),
        settlement_route,
    )
    return PersistedPublication(
        coordinate,
        frame,
        birth,
        provenance,
        settlement_reference,
        _publication_commitment(coordinate, frame, birth, provenance, settlement_reference),
    )


def _settlement_route_for(
    state: GraphRunState,
    coordinate: PublicationAvailabilityCoordinate[GraphValueT],
) -> GraphRouteId | None:
    activation = coordinate.activation
    identity = GraphActivationIdentity(
        activation.scope_run.graph_run_id,
        activation.superstep,
        activation.node_id,
    )
    matches = tuple(item for item in state.settled_publications if item.reference.activation == identity)
    if len(matches) != 1:
        raise SnapshotMismatchError("publication lacks its authoritative terminal settlement")
    return matches[0].reference.route


@dataclass(frozen=True, slots=True)
class GraphPersistenceWriteSet(Generic[GraphValueT]):
    commit_key: GraphCommitKey
    graph_inputs: tuple[PersistedGraphInput[GraphValueT], ...]
    publications: tuple[PersistedPublication[GraphValueT], ...]

    def __post_init__(self) -> None:
        commit_key = _admit_commit_key(self.commit_key)
        if (
            type(self.graph_inputs) is not tuple
            or len(self.graph_inputs) > 1
            or any(type(item) is not PersistedGraphInput for item in self.graph_inputs)
        ):
            raise SnapshotMismatchError("persistent graph input writes must be typed immutable records")
        if (
            type(self.publications) is not tuple
            or len(self.publications) > 1
            or any(type(item) is not PersistedPublication for item in self.publications)
        ):
            raise SnapshotMismatchError("persistent publication writes must be typed immutable records")
        graph_inputs = tuple(item.admit() for item in self.graph_inputs)
        publications = tuple(item.admit() for item in self.publications)
        if any(item.birth != commit_key for item in (*graph_inputs, *publications)):
            raise SnapshotMismatchError("persistent writes must be born in their enclosing commit")

    def admit(self) -> "GraphPersistenceWriteSet[GraphValueT]":
        if type(self) is not GraphPersistenceWriteSet:
            raise SnapshotMismatchError("persistent commit requires an exact complete write set")
        try:
            return GraphPersistenceWriteSet(self.commit_key, self.graph_inputs, self.publications)
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent write set is malformed") from error


@dataclass(frozen=True, slots=True)
class GraphPersistenceCommit(Generic[GraphValueT]):
    """An immutable CAS request; all equality-participating values are durable facts."""

    scope: tuple[GraphNodeId, ...]
    expected_revision: int | None
    candidate_state: GraphRunState
    writes: GraphPersistenceWriteSet[GraphValueT]
    agent_session: EncodedAgentSession | None = None
    session_receipt: GraphSessionReceipt | None = None
    expected_config_cursor: GraphConfigCursor | None = None

    def __post_init__(self) -> None:
        if self.expected_revision is not None and (
            type(self.expected_revision) is not int or self.expected_revision < 0
        ):
            raise SnapshotMismatchError("persistent commit expected revision must be absent or a non-negative integer")
        try:
            validate_graph_run_state(self.candidate_state)
        except (AttributeError, GraphStateTransitionError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent commit candidate is malformed") from error
        scope_run = ScopeRunCoordinate(self.scope, self.candidate_state.run_id)
        if type(self.writes) is not GraphPersistenceWriteSet:
            raise SnapshotMismatchError("persistent commit requires an exact complete write set")
        writes = self.writes.admit()
        agent_session = self.agent_session
        if agent_session is not None:
            try:
                agent_session = EncodedAgentSession.admit(agent_session)
            except (AttributeError, TypeError, ValueError) as error:
                raise SnapshotMismatchError("persistent commit AgentSession is malformed") from error
        session_receipt = self.session_receipt
        if session_receipt is not None:
            try:
                session_receipt = session_receipt.admit()
            except (AttributeError, SnapshotMismatchError, TypeError, ValueError) as error:
                raise SnapshotMismatchError("persistent commit Session receipt is malformed") from error
        key = writes.commit_key
        if key != GraphCommitKey(self.candidate_state.run_id, self.candidate_state.revision):
            raise SnapshotMismatchError("persistent write set is not bound to its candidate state")
        expected = None if self.candidate_state.revision == 0 else self.candidate_state.revision - 1
        if self.expected_revision != expected:
            raise SnapshotMismatchError("persistent commit CAS revision does not name its predecessor")
        _validate_persistent_config_session(
            self.candidate_state,
            self.expected_config_cursor,
            agent_session,
        )
        if self.candidate_state.graph_input_evidence is None or any(
            item.evidence is None for item in self.candidate_state.settled_publications
        ):
            raise SnapshotMismatchError("durable candidate state is missing value evidence commitments")
        if agent_session is None or session_receipt is None:
            if agent_session is not session_receipt:
                raise SnapshotMismatchError("persistent commit Session and receipt must be present together")
        else:
            expected_receipt = GraphSessionReceipt.for_commit(self.scope, key, agent_session)
            if session_receipt != expected_receipt:
                raise SnapshotMismatchError("persistent commit Session receipt is not bound to this exact commit")
        if self.candidate_state.revision == 0:
            if len(writes.graph_inputs) != 1:
                raise SnapshotMismatchError("durable StartGraphRun requires exactly one graph input")
            graph_input = writes.graph_inputs[0]
            if (
                graph_input.coordinate.scope_run != scope_run
                or graph_input.evidence != self.candidate_state.graph_input_evidence
            ):
                raise SnapshotMismatchError("durable graph input is not bound to its authoritative state")
        for publication in writes.publications:
            activation = publication.coordinate.activation
            if activation.scope_run != scope_run:
                raise SnapshotMismatchError("durable publication belongs to a different scoped run")
            identity = GraphActivationIdentity(
                activation.scope_run.graph_run_id,
                activation.superstep,
                activation.node_id,
            )
            matches = tuple(
                item for item in self.candidate_state.settled_publications if item.reference.activation == identity
            )
            if len(matches) != 1:
                raise SnapshotMismatchError("durable publication lacks its authoritative settlement")
            settlement = matches[0]
            token = require_publication_confirmation(publication.birth.revision, publication.provenance)
            if (
                settlement.reference != publication.settlement_reference
                or settlement.commit_revision != publication.birth.revision
                or settlement.execution != token
                or settlement.evidence != publication.evidence
            ):
                raise SnapshotMismatchError("durable publication does not match its authoritative settlement")

    def admit(self) -> "GraphPersistenceCommit[GraphValueT]":
        if type(self) is not GraphPersistenceCommit:
            raise SnapshotMismatchError("persistence must return an exact commit request")
        try:
            return GraphPersistenceCommit(
                self.scope,
                self.expected_revision,
                self.candidate_state,
                self.writes,
                self.agent_session,
                self.session_receipt,
                self.expected_config_cursor,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("persistent commit request is malformed") from error


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
    # Session is execution metadata and has its own atomic envelope; it must
    # never enter the ordinary graph-value codec payload.
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
    return EncodedFrame(
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
    agent_session: EncodedAgentSession | None = None
    session_codec: AgentSessionCodecCarrier | None = None

    def __post_init__(self) -> None:
        if type(self.codec) is not FrameCodec or not callable(self.writer):
            raise GraphValidationError("durable graph commit requires a typed codec and writer")
        self.codec.validate()
        if self.agent_session is not None:
            try:
                EncodedAgentSession.admit(self.agent_session)
            except (AttributeError, TypeError, ValueError) as error:
                raise GraphValidationError("durable graph commit AgentSession is malformed") from error
        if self.session_codec is not None:
            try:
                admit_session_codec_carrier(self.session_codec)
            except AgentSessionContractError as error:
                raise GraphValidationError("durable graph commit session codec is malformed") from error

    def admit(self) -> "DurableGraphCommit[GraphValueT]":
        if type(self) is not DurableGraphCommit:
            raise GraphValidationError("durable recovery requires its durable commit capability")
        try:
            if type(self.codec) is not FrameCodec or not callable(self.writer):
                raise GraphValidationError("durable graph commit requires a typed codec and writer")
            self.codec.validate()
            if self.agent_session is not None:
                EncodedAgentSession.admit(self.agent_session)
            if self.session_codec is not None:
                admit_session_codec_carrier(self.session_codec)
        except (AttributeError, TypeError, ValueError) as error:
            raise GraphValidationError("durable graph commit capability is malformed") from error
        return self

    async def __call__(self, transition: GraphTransition[GraphValueT], /) -> GraphRunState:
        writes = transition.writes
        graph_inputs = tuple(
            _capture_graph_input(
                evidence.coordinate,
                _encode_frame(evidence.frame, self.codec),
                writes.commit_key,
            )
            for evidence in writes.graph_inputs
        )
        publications = tuple(
            _capture_publication(
                evidence.coordinate,
                _encode_frame(evidence.frame, self.codec),
                writes.commit_key,
                evidence.provenance,
                _settlement_route_for(transition.candidate_state, evidence.coordinate),
            )
            for evidence in writes.publications
        )
        bound = bind_transition_evidence(
            transition,
            graph_input=graph_inputs[0].evidence if graph_inputs else None,
            publication=publications[0].evidence if publications else None,
        )
        session = transition.agent_session
        if session is None:
            encoded_session = self.agent_session
        else:
            codec_carrier = self.session_codec
            if codec_carrier is None:
                raise SnapshotMismatchError("a Session successor requires its session codec")
            try:
                encoded_session = encode_session_carrier(codec_carrier, session)
            except AgentSessionContractError as error:
                raise SnapshotMismatchError("AgentSession successor could not be encoded") from error
        scope = tuple(GraphNodeId(segment) for segment in transition.scope)
        session_receipt = (
            None
            if encoded_session is None
            else GraphSessionReceipt.for_commit(scope, writes.commit_key, encoded_session)
        )
        request = GraphPersistenceCommit(
            scope,
            transition.previous_state.revision if transition.previous_state is not None else None,
            bound.candidate_state,
            GraphPersistenceWriteSet(
                writes.commit_key,
                graph_inputs,
                publications,
            ),
            encoded_session,
            session_receipt,
            transition.previous_state.config_cursor if transition.previous_state is not None else None,
        )
        baseline = deepcopy(request.admit())
        confirmed = await self.writer(request)
        if request.admit() != baseline:
            raise SnapshotMismatchError("persistence must confirm the exact state and complete value write set")
        if type(confirmed) is not GraphPersistenceCommit or confirmed.admit() != baseline:
            raise SnapshotMismatchError("persistence must confirm the exact state and complete value write set")
        return bound.candidate_state


@dataclass(frozen=True, slots=True)
class GraphCheckpoint(Generic[GraphValueT]):
    """One consistent persistence read, not another runtime state model."""

    root_state: GraphRunState
    child_runs: tuple[ScopedRunEvidence, ...]
    graph_inputs: tuple[PersistedGraphInput[GraphValueT], ...]
    publications: tuple[PersistedPublication[GraphValueT], ...]
    agent_session: EncodedAgentSession | None = None
    session_receipt: GraphSessionReceipt | None = None

    def __post_init__(self) -> None:
        try:
            validate_graph_run_state(self.root_state)
        except (AttributeError, GraphStateTransitionError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("checkpoint requires an authoritative root state") from error
        if type(self.child_runs) is not tuple:
            raise SnapshotMismatchError("checkpoint child states must be typed immutable bindings")
        admitted_children: list[ScopedRunEvidence] = []
        for child in self.child_runs:
            try:
                if type(child) is ScopedStateBinding:
                    scope_run = ScopeRunCoordinate(tuple(child.scope_run.scope), child.scope_run.graph_run_id)
                    validate_graph_run_state(child.state)
                    admitted = ScopedStateBinding(scope_run, child.state)
                    _ = admitted.parent_activation
                elif type(child) is UncreatedGraphRun:
                    admitted = UncreatedGraphRun(
                        ScopeRunCoordinate(tuple(child.scope_run.scope), child.scope_run.graph_run_id)
                    )
                else:
                    raise SnapshotMismatchError("checkpoint child state has an unsupported variant")
            except (AttributeError, GraphStateTransitionError, SnapshotMismatchError, TypeError, ValueError) as error:
                raise SnapshotMismatchError("checkpoint child states must be typed immutable bindings") from error
            admitted_children.append(admitted)
        if tuple(admitted_children) != tuple(sorted(admitted_children, key=lambda item: item.scope_run)):
            raise SnapshotMismatchError("checkpoint child evidence must be canonical and distinct")
        if len({item.scope_run for item in admitted_children}) != len(admitted_children):
            raise SnapshotMismatchError("checkpoint child evidence repeats one scoped run")
        if type(self.graph_inputs) is not tuple or any(
            type(item) is not PersistedGraphInput for item in self.graph_inputs
        ):
            raise SnapshotMismatchError("checkpoint graph inputs must be typed immutable records")
        if type(self.publications) is not tuple or any(
            type(item) is not PersistedPublication for item in self.publications
        ):
            raise SnapshotMismatchError("checkpoint publications must be typed immutable records")
        agent_session = self.agent_session
        if agent_session is not None:
            try:
                agent_session = EncodedAgentSession.admit(agent_session)
            except (AttributeError, TypeError, ValueError) as error:
                raise SnapshotMismatchError("checkpoint AgentSession is malformed") from error
            family_cursors = {
                self.root_state.config_cursor,
                *(item.state.config_cursor for item in self.child_runs if isinstance(item, ScopedStateBinding)),
            }
            if agent_session.config_cursor is not None and agent_session.config_cursor not in family_cursors:
                raise SnapshotMismatchError("checkpoint AgentSession Config is not bound to the graph family")
        session_receipt = self.session_receipt
        if session_receipt is not None:
            try:
                session_receipt = session_receipt.admit()
            except (AttributeError, SnapshotMismatchError, TypeError, ValueError) as error:
                raise SnapshotMismatchError("checkpoint Session receipt is malformed") from error
        if agent_session is None or session_receipt is None:
            if agent_session is not session_receipt:
                raise SnapshotMismatchError("checkpoint Session and receipt must be present together")
        else:
            expected_receipt = GraphSessionReceipt.for_commit(
                session_receipt.scope,
                session_receipt.commit_key,
                agent_session,
            )
            if session_receipt != expected_receipt:
                raise SnapshotMismatchError("checkpoint Session receipt does not match its AgentSession")
            family_states = {
                ScopeRunCoordinate((), self.root_state.run_id): self.root_state,
                **{item.scope_run: item.state for item in admitted_children if isinstance(item, ScopedStateBinding)},
            }
            receipt_scope = ScopeRunCoordinate(tuple(session_receipt.scope), session_receipt.commit_key.run_id)
            source_state = family_states.get(receipt_scope)
            if source_state is None or source_state.revision != session_receipt.commit_key.revision:
                raise SnapshotMismatchError("checkpoint Session receipt is not bound to an included family commit")
        graph_inputs = tuple(item.admit() for item in self.graph_inputs)
        publications = tuple(item.admit() for item in self.publications)
        if graph_inputs != tuple(sorted(graph_inputs, key=lambda item: item.coordinate)):
            raise SnapshotMismatchError("checkpoint graph inputs must be canonical and distinct")
        if publications != tuple(sorted(publications, key=lambda item: item.coordinate)):
            raise SnapshotMismatchError("checkpoint publications must be canonical and distinct")
        if len({item.coordinate for item in graph_inputs}) != len(graph_inputs):
            raise SnapshotMismatchError("checkpoint repeats one graph input coordinate")
        if len({item.coordinate for item in publications}) != len(publications):
            raise SnapshotMismatchError("checkpoint repeats one publication coordinate")

    def admit(self) -> "GraphCheckpoint[GraphValueT]":
        if type(self) is not GraphCheckpoint:
            raise SnapshotMismatchError("checkpoint must be an exact typed record")
        try:
            return GraphCheckpoint(
                self.root_state,
                self.child_runs,
                self.graph_inputs,
                self.publications,
                self.agent_session,
                self.session_receipt,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("checkpoint is malformed") from error

    @property
    def config_cursors(self) -> tuple[GraphConfigCursor, ...]:
        states = (self.root_state, *(item.state for item in self.child_runs if isinstance(item, ScopedStateBinding)))
        try:
            for state in states:
                validate_graph_run_state(state)
        except GraphStateTransitionError as error:
            raise SnapshotMismatchError("checkpoint contains a malformed authoritative state") from error
        cursors = {state.config_cursor for state in states if state.config_digest is not None}
        cursors.update(
            item.frame.config_cursor
            for item in (*self.graph_inputs, *self.publications)
            if item.frame.config_cursor is not None
        )
        if self.agent_session is not None and self.agent_session.config_cursor is not None:
            cursors.add(self.agent_session.config_cursor)
        return tuple(sorted(cursors))

    def admit_child_reads(
        self,
        loaded: "GraphCheckpoint[GraphValueT]",
        children: tuple[ScopeRunCoordinate, ...],
    ) -> "GraphCheckpoint[GraphValueT]":
        current = self.admit()
        loaded = loaded.admit()
        previous_states = tuple(item for item in current.child_runs if isinstance(item, ScopedStateBinding))
        loaded_states = tuple(item for item in loaded.child_runs if isinstance(item, ScopedStateBinding))
        previous_family = GraphCheckpoint(
            current.root_state,
            previous_states,
            current.graph_inputs,
            current.publications,
            current.agent_session,
            current.session_receipt,
        )
        loaded_family = GraphCheckpoint(
            loaded.root_state,
            loaded_states,
            loaded.graph_inputs,
            loaded.publications,
            loaded.agent_session,
            loaded.session_receipt,
        )
        if previous_family != loaded_family:
            raise SnapshotMismatchError("family facts changed during an authority-constrained child reread")
        expected = {item.scope_run for item in current.child_runs if isinstance(item, UncreatedGraphRun)} | set(
            children
        )
        actual = tuple(item.scope_run for item in loaded.child_runs if isinstance(item, UncreatedGraphRun))
        if len(actual) != len(expected) or set(actual) != expected:
            raise SnapshotMismatchError("child reread must prove exactly the requested negative reads")
        return loaded


@dataclass(frozen=True, slots=True)
class GraphRecovery(Generic[GraphValueT]):
    """Agent-supplied read material plus exact, already resolved Config capabilities."""

    checkpoint: GraphCheckpoint[GraphValueT]
    commit: DurableGraphCommit[GraphValueT]
    configs: tuple[Config, ...] = ()
    session: AgentSessionCarrier | None = None

    def __post_init__(self) -> None:
        if type(self.checkpoint) is not GraphCheckpoint:
            raise SnapshotMismatchError("durable recovery requires an exact typed checkpoint")
        if type(self.commit) is not DurableGraphCommit:
            raise GraphValidationError("durable recovery requires its durable commit capability")
        checkpoint = self.checkpoint.admit()
        commit = self.commit.admit()
        if checkpoint.agent_session != commit.agent_session:
            raise SnapshotMismatchError("durable recovery checkpoint and commit must bind the same AgentSession")
        session = self.session
        if session is not None:
            try:
                session = admit_session_carrier(session)
            except (AttributeError, TypeError, ValueError) as error:
                raise SnapshotMismatchError("durable recovery Session is malformed") from error
        if (checkpoint.agent_session is None) != (session is None):
            raise SnapshotMismatchError("durable recovery requires a decoded Session for its envelope")
        if session is not None:
            envelope = checkpoint.agent_session
            if envelope is None or session.config_cursor != envelope.config_cursor:
                raise SnapshotMismatchError("durable recovery Session does not match its envelope Config cursor")
            codec = commit.session_codec
            if codec is None:
                raise SnapshotMismatchError("durable recovery Session requires its codec")
            try:
                encoded = encode_session_carrier(codec, session)
            except AgentSessionContractError as error:
                raise SnapshotMismatchError("durable recovery Session could not be canonically encoded") from error
            if encoded != envelope:
                raise SnapshotMismatchError("durable recovery Session does not match its durable envelope")
        if type(self.configs) is not tuple:
            raise SnapshotMismatchError("recovery Config capabilities must be an immutable tuple")

    def admit(self) -> "GraphRecovery[GraphValueT]":
        if type(self) is not GraphRecovery:
            raise SnapshotMismatchError("durable recovery requires an exact typed capability")
        try:
            return GraphRecovery(self.checkpoint, self.commit, self.configs, self.session)
        except (AttributeError, TypeError, ValueError) as error:
            raise SnapshotMismatchError("durable recovery capability is malformed") from error


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
    frame = EncodedFrame.admit(frame)
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

    recovery = recovery.admit()
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
        if binding.state.graph_input_evidence is None or any(
            settlement.evidence is None for settlement in binding.state.settled_publications
        ):
            raise SnapshotMismatchError("durable state is missing its value evidence commitments")
    bindings = {binding.scope_run: binding.state for binding in lineage.bindings}
    for graph_input in checkpoint.graph_inputs:
        state = bindings.get(graph_input.coordinate.scope_run)
        if state is None or state.graph_input_evidence != graph_input.evidence:
            raise SnapshotMismatchError("persistent graph input is not bound to its authoritative state")
    for publication in checkpoint.publications:
        activation = publication.coordinate.activation
        state = bindings.get(activation.scope_run)
        identity = GraphActivationIdentity(
            activation.scope_run.graph_run_id,
            activation.superstep,
            activation.node_id,
        )
        matches = (
            ()
            if state is None
            else tuple(item for item in state.settled_publications if item.reference.activation == identity)
        )
        token = require_publication_confirmation(publication.birth.revision, publication.provenance)
        if (
            len(matches) != 1
            or matches[0].reference != publication.settlement_reference
            or matches[0].commit_revision != publication.birth.revision
            or matches[0].execution != token
            or matches[0].evidence != publication.evidence
        ):
            raise SnapshotMismatchError("persistent publication is not bound to its authoritative settlement")
    frames: ScopedFrameIndex[GraphValueT] = ScopedFrameIndex()
    try:
        for graph_input in checkpoint.graph_inputs:
            coordinate = graph_input.coordinate
            scoped_graph = _compiled_graph_at_scope(graph, coordinate.scope_run.scope)
            values, config = _decode_frame(graph_input.frame, recovery.commit.codec, configs)
            frame = _make_graph_input_frame(
                values,
                scoped_graph.graph_input_descriptor.declarations,
                config,
            )
            frames = frames.add_graph_input(AdmittedGraphInput(coordinate, frame))
        for publication in checkpoint.publications:
            publication_coordinate = publication.coordinate
            scoped_graph = _compiled_graph_at_scope(graph, publication_coordinate.activation.scope_run.scope)
            descriptor = scoped_graph.transition.publications[publication_coordinate.activation.node_id]
            values, config = _decode_frame(publication.frame, recovery.commit.codec, configs)
            output = _make_node_output_frame(values, descriptor.declarations, config)
            frames = frames.add_publication(
                ConfirmedPublication(
                    publication_coordinate,
                    output,
                    publication.birth.revision,
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
                    project_graph_outputs(
                        scoped_graph,
                        binding.scope_run,
                        binding.state.superstep,
                        frames,
                        owner_session=recovery.session,
                    ),
                )
            )
        validate_context(graph, lineage, frames, recovered=False)
    except (GraphValueAdmissionError, GraphValuePublicationError) as error:
        raise SnapshotMismatchError("persistent value evidence cannot be admitted by the compiled graph") from error
    return frames


__all__: list[str] = []

"""The sole authority, persistence and Config wiring boundary for Agent runs."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Generic, Never, TypeAlias, TypeVar, cast

from typing_extensions import TypeVar as DefaultTypeVar

from mote_kernel.config import (
    Config,
    ConfigContractError,
    ConfigResolver,
    ConfigSnapshotKey,
    ConfigSnapshotStore,
    load_config_snapshot,
    require_config_resolver,
    require_config_store,
    resolve_config,
)
from mote_kernel.execution import Graph
from mote_kernel.execution.cancellation import wait_for_owner_task
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.execution.graph.values import _require_graph_values
from mote_kernel.execution.graph_result import GraphAbortView, GraphFailureView, GraphInterruptView
from mote_kernel.execution.limits import ExecutionLimits
from mote_kernel.execution.persistence import (
    DurableGraphCommit,
    GraphCheckpoint,
    GraphPersistenceCommit,
    GraphRecovery,
)
from mote_kernel.persistence import (
    AgentRunKey,
    AuthorityPort,
    CommitApplied,
    CommitAttemptsExhaustedError,
    CommitNotApplied,
    CommitUnknown,
    CommitUnresolvedError,
    ExecutionAuthority,
    NeverCreated,
    PersistenceConflictError,
    PersistenceContractError,
    PersistencePort,
)
from mote_kernel.session import (
    AgentSession,
    AgentSessionCodec,
    AgentSessionContractError,
    EncodedAgentSession,
    admit_session_carrier,
)
from mote_kernel.state.graph_state import GraphConfigCursor, GraphRunId
from mote_kernel.state.graph_state.identity import is_canonical_identity

GraphValueT = TypeVar("GraphValueT")
AgentHookStateT = DefaultTypeVar("AgentHookStateT", default=Never)
AgentContextT = DefaultTypeVar("AgentContextT", default=Never)


class AgentContractError(ValueError):
    """Agent assembly or a business request violates its typed contract."""


class AgentRunNotFoundError(LookupError):
    """An explicit continuation request names a run that was never created."""


def _admit_business_values(values: Graph.Values[GraphValueT]) -> None:
    values = _require_graph_values(values)
    if values.activation_config is not None:
        raise AgentContractError("Agent business input cannot replace activation Config")


@dataclass(frozen=True, slots=True)
class AgentStart(Generic[GraphValueT, AgentHookStateT, AgentContextT]):
    run_id: str
    values: Graph.Values[GraphValueT]
    session: AgentSession[AgentHookStateT, AgentContextT] | None = None


@dataclass(frozen=True, slots=True)
class AgentAnswer(Generic[GraphValueT]):
    interrupt: GraphInterruptView
    values: Graph.Values[GraphValueT]

    def __post_init__(self) -> None:
        try:
            GraphInterruptView.admit(self.interrupt)
            _admit_business_values(self.values)
        except AgentContractError:
            raise
        except (AttributeError, Graph.SnapshotMismatchError, Graph.ValueAdmissionError, TypeError, ValueError) as error:
            raise AgentContractError("an Agent answer requires an exact interrupt and business values") from error

    def admit(self) -> "AgentAnswer[GraphValueT]":
        if type(self) is not AgentAnswer:
            raise AgentContractError("Agent answers must contain exact typed records")
        try:
            return AgentAnswer(GraphInterruptView.admit(self.interrupt), _require_graph_values(self.values))
        except AgentContractError:
            raise
        except (AttributeError, Graph.SnapshotMismatchError, Graph.ValueAdmissionError, TypeError, ValueError) as error:
            raise AgentContractError("Agent answer is malformed") from error


@dataclass(frozen=True, slots=True)
class AgentResume(Generic[GraphValueT]):
    run_id: str
    answers: tuple[AgentAnswer[GraphValueT], ...] = ()

    def __post_init__(self) -> None:
        if type(self.answers) is not tuple or any(type(answer) is not AgentAnswer for answer in self.answers):
            raise AgentContractError("Agent answers must be an immutable typed tuple")
        for answer in self.answers:
            answer.admit()


AgentRequest: TypeAlias = AgentStart[GraphValueT, AgentHookStateT, AgentContextT] | AgentResume[GraphValueT]


@dataclass(frozen=True, slots=True)
class AgentCompleted(Generic[GraphValueT, AgentHookStateT, AgentContextT]):
    run_id: str
    outputs: Graph.Values[GraphValueT]
    session: AgentSession[AgentHookStateT, AgentContextT] | None = None


@dataclass(frozen=True, slots=True)
class AgentFailed(Generic[AgentHookStateT, AgentContextT]):
    run_id: str
    failures: tuple[GraphFailureView, ...]
    interrupts: tuple[GraphInterruptView, ...]
    session: AgentSession[AgentHookStateT, AgentContextT] | None = None


@dataclass(frozen=True, slots=True)
class AgentInterrupted(Generic[AgentHookStateT, AgentContextT]):
    run_id: str
    interrupts: tuple[GraphInterruptView, ...]
    session: AgentSession[AgentHookStateT, AgentContextT] | None = None


@dataclass(frozen=True, slots=True)
class AgentAborted(Generic[AgentHookStateT, AgentContextT]):
    run_id: str
    abort: GraphAbortView
    session: AgentSession[AgentHookStateT, AgentContextT] | None = None


AgentResult: TypeAlias = (
    AgentCompleted[GraphValueT, AgentHookStateT, AgentContextT]
    | AgentFailed[AgentHookStateT, AgentContextT]
    | AgentInterrupted[AgentHookStateT, AgentContextT]
    | AgentAborted[AgentHookStateT, AgentContextT]
)


def _project_result(
    run_id: str,
    result: Graph.Result[GraphValueT],
) -> AgentResult[GraphValueT, AgentHookStateT, AgentContextT]:
    admitted = None if result.session is None else admit_session_carrier(result.session)
    session = cast(AgentSession[AgentHookStateT, AgentContextT] | None, admitted)
    if isinstance(result, Graph.CompletedResult):
        return AgentCompleted(run_id, Graph.values(**dict(result.outputs.items())), session)
    if isinstance(result, Graph.FailedResult):
        return AgentFailed(run_id, result.failures, result.interrupts, session)
    if isinstance(result, Graph.AbortedResult):
        return AgentAborted(run_id, result.abort, session)
    return AgentInterrupted(run_id, result.interrupts, session)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Exact Config readers and optional initial selection, never a latest cache."""

    store: ConfigSnapshotStore
    resolver: ConfigResolver
    initial: ConfigSnapshotKey | None = None

    def __post_init__(self) -> None:
        require_config_store(self.store)
        require_config_resolver(self.resolver)
        if self.initial is not None:
            if type(self.initial) is not ConfigSnapshotKey:
                raise ConfigContractError("Agent initial Config requires an exact snapshot key")
            replace(self.initial)


@dataclass(frozen=True, slots=True)
class _AuthorizedGraphWriter(Generic[GraphValueT]):
    persistence: PersistencePort[GraphValueT]
    authority: ExecutionAuthority
    max_attempts: int

    async def __call__(self, request: GraphPersistenceCommit[GraphValueT], /) -> GraphPersistenceCommit[GraphValueT]:
        try:
            baseline = deepcopy(request.admit())
        except Graph.SnapshotMismatchError as error:
            raise PersistenceContractError("Agent received a malformed Graph commit request") from error
        for _attempt in range(self.max_attempts):
            try:
                outcome = await self.persistence.commit(self.authority, request)
            finally:
                try:
                    unchanged = request.admit() == baseline
                except Graph.SnapshotMismatchError as error:
                    raise PersistenceContractError("persistence mutated the Graph commit request") from error
                if not unchanged:
                    raise PersistenceContractError("persistence mutated the Graph commit request")
            if type(outcome) is CommitUnknown:
                try:
                    outcome = await self.persistence.reconcile(self.authority, request)
                finally:
                    try:
                        unchanged = request.admit() == baseline
                    except Graph.SnapshotMismatchError as error:
                        raise PersistenceContractError(
                            "persistence mutated the reconciled Graph commit request"
                        ) from error
                    if not unchanged:
                        raise PersistenceContractError("persistence mutated the reconciled Graph commit request")
            if type(outcome) is CommitApplied:
                return outcome.admit().confirmed
            if type(outcome) is CommitUnknown:
                raise CommitUnresolvedError("the exact Graph commit remains unknown after reconciliation")
            if type(outcome) is not CommitNotApplied:
                raise PersistenceContractError("persistence returned an unsupported commit outcome")
        raise CommitAttemptsExhaustedError("the exact Graph commit exhausted its NotApplied attempt budget")


@dataclass(frozen=True, slots=True)
class Agent(Generic[GraphValueT, AgentHookStateT, AgentContextT]):
    """Run one explicitly identified task without retaining any runtime snapshot.

    Same-key concurrency is arbitrated by the required AuthorityPort, including
    calls through this same instance. Graph alone owns execution and task cleanup;
    Agent releases authority only after Graph has returned or raised.
    """

    agent_id: str
    assemble: Callable[[Config | None], Graph[GraphValueT]]
    codec: FrameCodec[GraphValueT]
    persistence: PersistencePort[GraphValueT]
    authority: AuthorityPort
    config: AgentConfig | None = None
    max_commit_attempts: int = 3
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)
    session_codec: AgentSessionCodec[AgentHookStateT, AgentContextT] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.agent_id) or not callable(self.assemble):
            raise AgentContractError("Agent requires a canonical identity and Graph assembly capability")
        if type(self.codec) is not FrameCodec:
            raise AgentContractError("Agent requires a typed frame codec")
        self.codec.validate()
        try:
            persistence_operations = (self.persistence.load, self.persistence.commit, self.persistence.reconcile)
        except AttributeError as error:
            raise AgentContractError("Agent requires load, commit and reconcile persistence capabilities") from error
        if not all(callable(operation) for operation in persistence_operations):
            raise AgentContractError("Agent requires load, commit and reconcile persistence capabilities")
        try:
            authority_operations = (self.authority.acquire, self.authority.release)
        except AttributeError as error:
            raise AgentContractError("Agent requires acquire and release authority capabilities") from error
        if not all(callable(operation) for operation in authority_operations):
            raise AgentContractError("Agent requires acquire and release authority capabilities")
        if self.config is not None:
            if type(self.config) is not AgentConfig:
                raise AgentContractError("Agent Config capabilities must be assembled together")
            replace(self.config)
        if self.session_codec is not None:
            if type(self.session_codec) is not AgentSessionCodec:
                raise AgentContractError("Agent session codec must be an exact typed codec")
            replace(self.session_codec)
        if type(self.max_commit_attempts) is not int or self.max_commit_attempts < 1:
            raise AgentContractError("Agent commit attempts must be an exact positive integer")
        if type(self.limits) is not ExecutionLimits:
            raise AgentContractError("Agent requires typed execution limits")
        replace(self.limits)

    async def _recover_configs(
        self, checkpoint: GraphCheckpoint[GraphValueT]
    ) -> tuple[Config | None, tuple[Config, ...]]:
        cursors = checkpoint.config_cursors
        if not cursors:
            return None, ()
        capabilities = self.config
        if capabilities is None:
            raise AgentContractError("persisted Config requires its snapshot store and resolver")
        configs: dict[GraphConfigCursor, Config] = {}
        for cursor in cursors:
            key = ConfigSnapshotKey(cursor.definition_id, cursor.definition_version, cursor.revision)
            snapshot = await load_config_snapshot(capabilities.store, key)
            if snapshot.config_cursor != cursor:
                raise ConfigContractError("Agent recovery requires the exact persisted Config digest")
            configs[cursor] = await resolve_config(capabilities.resolver, snapshot)
        return configs.get(checkpoint.root_state.config_cursor), tuple(configs.values())

    @staticmethod
    def _session_config(
        encoded: EncodedAgentSession,
        current_config: Config | None,
        configs: tuple[Config, ...],
    ) -> Config | None:
        """Resolve the Config named by the caller-owned Session envelope."""

        for config in (current_config, *configs):
            if config is not None and config.config_cursor == encoded.config_cursor:
                return config
        if encoded.config_cursor is None:
            return None
        raise ConfigContractError("persisted AgentSession requires its exact Config snapshot")

    async def _run_authorized(
        self,
        request: AgentRequest[GraphValueT, AgentHookStateT, AgentContextT],
        authority: ExecutionAuthority,
    ) -> AgentResult[GraphValueT, AgentHookStateT, AgentContextT]:
        loaded = await self.persistence.load(authority)
        configs: tuple[Config, ...] = ()
        current_config: Config | None = None
        current_session: AgentSession[AgentHookStateT, AgentContextT] | None = None
        encoded_session: EncodedAgentSession | None = None
        if type(loaded) is NeverCreated:
            if not isinstance(request, AgentStart):
                raise AgentRunNotFoundError("cannot continue an Agent run that was never created")
            if request.session is not None:
                current_session = request.session
                current_config = current_session.config
                if self.config is not None and self.config.initial is not None:
                    raise AgentContractError("AgentStart session and Agent initial Config cannot both be supplied")
            elif self.config is not None and self.config.initial is not None:
                snapshot = await load_config_snapshot(self.config.store, self.config.initial)
                current_config = await resolve_config(self.config.resolver, snapshot)
        elif type(loaded) is GraphCheckpoint:
            try:
                loaded = loaded.admit()
            except Graph.SnapshotMismatchError as error:
                raise PersistenceContractError("persistence returned a malformed checkpoint") from error
            if loaded.root_state.run_id != authority.run.run_id:
                raise Graph.SnapshotMismatchError("loaded checkpoint belongs to a different Agent run")
            if isinstance(request, AgentStart):
                raise PersistenceConflictError("cannot create an Agent run that already exists")
            current_config, configs = await self._recover_configs(loaded)
            if loaded.agent_session is not None:
                codec = self.session_codec
                if codec is None:
                    raise AgentContractError("persisted AgentSession requires its session codec")
                session_config = self._session_config(loaded.agent_session, current_config, configs)
                try:
                    current_session = codec.decode(loaded.agent_session, session_config)
                except AgentSessionContractError as error:
                    raise PersistenceContractError("persisted AgentSession is malformed") from error
                encoded_session = loaded.agent_session
        else:
            raise PersistenceContractError("load must return a checkpoint or explicit NeverCreated evidence")
        graph = self.assemble(current_config)
        if type(graph) is not Graph:
            raise AgentContractError("Agent assembly must return the Graph facade")
        if current_session is not None and encoded_session is None:
            codec = self.session_codec
            if codec is None:
                raise AgentContractError("AgentStart session requires its session codec")
            try:
                encoded_session = codec.encode(current_session)
            except AgentSessionContractError as error:
                raise AgentContractError("AgentSession could not be encoded") from error
        commit = DurableGraphCommit(
            self.codec,
            _AuthorizedGraphWriter(self.persistence, authority, self.max_commit_attempts),
            encoded_session,
            self.session_codec,
        )
        try:
            if isinstance(request, AgentResume):
                checkpoint = cast(GraphCheckpoint[GraphValueT], loaded)
                children = graph.recovery_child_reads(checkpoint)
                if children:
                    reread = await self.persistence.load(authority, children=children)
                    if type(reread) is not GraphCheckpoint:
                        raise Graph.SnapshotMismatchError("child reread lost the authoritative family")
                    checkpoint = checkpoint.admit_child_reads(reread.admit(), children)
                actions = tuple(
                    graph.resume_interrupted(
                        answer.interrupt.node_id,
                        answer.interrupt.interrupt_id,
                        answer.values,
                        scope=answer.interrupt.scope,
                    )
                    for answer in request.answers
                )
                result = await graph.run(
                    recovery=GraphRecovery(checkpoint, commit, configs, current_session),
                    resume=actions,
                    max_supersteps=self.limits.max_supersteps,
                    max_parallel_tasks=self.limits.max_parallel_tasks,
                )
            else:
                result = await graph.run(
                    request.values,
                    run_id=request.run_id,
                    activation_config=current_config,
                    session=current_session,
                    commit=commit,
                    max_supersteps=self.limits.max_supersteps,
                    max_parallel_tasks=self.limits.max_parallel_tasks,
                )
        except Exception as error:
            if isinstance(error, Graph.PartialCommitError):
                raise cast(Graph.PartialCommitError[GraphValueT], error).cause from None
            raise
        return _project_result(request.run_id, result)

    async def run(
        self, request: AgentRequest[GraphValueT, AgentHookStateT, AgentContextT], /
    ) -> AgentResult[GraphValueT, AgentHookStateT, AgentContextT]:
        if type(request) not in (AgentStart, AgentResume):
            raise AgentContractError("Agent.run requires a typed start or resume request")
        try:
            if isinstance(request, AgentStart):
                _admit_business_values(request.values)
                session = request.session
                if session is not None:
                    try:
                        session = AgentSession[AgentHookStateT, AgentContextT].admit(session)
                    except AgentSessionContractError as error:
                        raise AgentContractError("AgentStart session is malformed") from error
                request = AgentStart(request.run_id, request.values, session)
            else:
                request = AgentResume(request.run_id, request.answers)
        except AgentContractError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise AgentContractError("Agent.run received a malformed request") from error
        key = AgentRunKey(self.agent_id, GraphRunId(request.run_id))
        authority, acquisition_cancellation = await wait_for_owner_task(
            asyncio.create_task(self.authority.acquire(key))
        )
        release_authority = authority if type(authority) is ExecutionAuthority else None
        primary: BaseException | None = None
        try:
            admitted_authority = ExecutionAuthority.admit(authority)
            if admitted_authority.run != key:
                raise PersistenceContractError("acquired authority belongs to a different Agent run")
            if acquisition_cancellation is not None:
                raise acquisition_cancellation
            return await self._run_authorized(request, authority)
        except BaseException as error:
            primary = error
            raise
        finally:
            if release_authority is not None:
                try:
                    _, release_cancellation = await wait_for_owner_task(
                        asyncio.create_task(self.authority.release(release_authority))
                    )
                    if release_cancellation is not None:
                        raise release_cancellation
                except BaseException as release_error:
                    if primary is None:
                        raise
                    raise primary from release_error


__all__ = ["Agent"]

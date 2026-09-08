"""The complete runtime Config and its node-owned narrow projections.

``ConfigSnapshot`` is the immutable declaration.  A resolver turns one exact
snapshot into the complete runtime ``Config`` (ports, invocations and typed
domain projections).  Execution carries that complete value as activation
metadata, while every node calls ``Config.bind(...)`` for only its declared
projection.  A successor produced by Observe therefore takes effect on the
next activation of the same compiled graph without entering business DTOs,
crossing Invocation boundaries, or reassembling ReAct.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Generic, NewType, Protocol, Self, TypeVar, cast, runtime_checkable

from mote_kernel.state.graph_state import GraphConfigCursor, GraphRunState
from mote_kernel.state.graph_state.identity import (
    GraphDefinitionId,
    GraphDefinitionVersion,
    is_canonical_identity,
)

ConfigSnapshotDigest = NewType("ConfigSnapshotDigest", str)
BoundT_co = TypeVar("BoundT_co", covariant=True)
ProjectionT = TypeVar("ProjectionT", bound="ConfigSlice")
ActivationValueT = TypeVar("ActivationValueT")


class ConfigContractError(ValueError):
    """Raised when a config snapshot, projection, or recovery join is invalid."""


@dataclass(frozen=True, slots=True, order=True)
class ConfigSnapshotKey:
    """Exact immutable config revision referenced by a graph run."""

    definition_id: GraphDefinitionId
    definition_version: GraphDefinitionVersion
    revision: int = 1

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.definition_id):
            raise ConfigContractError("config snapshot definition_id must be canonical")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ConfigContractError("config snapshot definition_version must be a positive integer")
        if type(self.revision) is not int or self.revision < 1:
            raise ConfigContractError("config snapshot revision must be a positive integer")

    @classmethod
    def from_state(cls, state: GraphRunState, /) -> Self:
        """Project the only config key accepted when recovering ``state``."""

        if type(state) is not GraphRunState:
            raise ConfigContractError("config recovery requires an exact GraphRunState")
        try:
            # The compiled topology is still validated at this boundary so a
            # malformed state cannot be smuggled through merely because its
            # independent Config cursor happens to be well formed.  These
            # fields are validation context only; they are never used as the
            # returned Config identity.
            if not is_canonical_identity(state.definition_id):
                raise ConfigContractError("graph state topology definition_id is malformed")
            if type(state.definition_version) is not int or state.definition_version < 1:
                raise ConfigContractError("graph state topology version is malformed")
            # ``definition_id``/``definition_version`` identify the compiled
            # topology.  They are not the Config identity: Observe may move a
            # live run to a new immutable Config revision while retaining the
            # same compiled graph.  Read only the state-owned Config fields.
            definition_id = state.config_definition_id
            definition_version = state.config_definition_version
            revision = state.config_revision
            if definition_id is None or definition_version is None:
                raise ConfigContractError("graph state config identity is missing")
            return cls(definition_id, definition_version, revision)
        except ConfigContractError:
            raise
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
            raise ConfigContractError("graph state config identity is malformed") from error


def _revalidate_snapshot_key(key: ConfigSnapshotKey, field: str, /) -> ConfigSnapshotKey:
    """Reconstruct a key at every boundary that can receive decoded values."""

    try:
        definition_id = key.definition_id
        definition_version = key.definition_version
        revision = key.revision
    except AttributeError as error:
        raise ConfigContractError(f"{field} is malformed") from error
    return ConfigSnapshotKey(definition_id, definition_version, revision)


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Complete serializable config document stored once per immutable key.

    ``payload`` is an owner-defined canonical encoding, normally JSON or a
    versioned binary schema.  Kernel does not inspect it.  The digest is part
    of the persisted envelope so corruption is rejected before capability
    resolution.
    """

    key: ConfigSnapshotKey
    payload: bytes
    digest: ConfigSnapshotDigest

    def __post_init__(self) -> None:
        if type(self.key) is not ConfigSnapshotKey:
            raise ConfigContractError("config snapshot requires a ConfigSnapshotKey")
        _revalidate_snapshot_key(self.key, "config snapshot key")
        if type(self.payload) is not bytes or not self.payload:
            raise ConfigContractError("config snapshot payload must be non-empty exact bytes")
        expected = ConfigSnapshotDigest(sha256(self.payload).hexdigest())
        if type(self.digest) is not str or self.digest != expected:
            raise ConfigContractError("config snapshot digest does not match its payload")

    @classmethod
    def capture(cls, key: ConfigSnapshotKey, payload: bytes, /) -> Self:
        """Capture one content-address-checked immutable snapshot."""

        if type(key) is not ConfigSnapshotKey:
            raise ConfigContractError("config snapshot requires a ConfigSnapshotKey")
        key = _revalidate_snapshot_key(key, "config snapshot key")
        if type(payload) is not bytes or not payload:
            raise ConfigContractError("config snapshot payload must be non-empty exact bytes")
        return cls(key, payload, ConfigSnapshotDigest(sha256(payload).hexdigest()))

    @property
    def config_cursor(self) -> GraphConfigCursor:
        """Return the state-neutral coordinate for this immutable snapshot."""

        return GraphConfigCursor(
            self.key.definition_id,
            self.key.definition_version,
            self.key.revision,
            str(self.digest),
        )


def _revalidate_snapshot(snapshot: ConfigSnapshot, field: str, /) -> ConfigSnapshot:
    """Reconstruct a snapshot at an asynchronous persistence boundary.

    A deserializer can bypass a dataclass ``__post_init__`` (or a caller can
    use ``object.__new__``).  Reconstructing the exact immutable envelope keeps
    malformed values in the Config contract instead of leaking an
    ``AttributeError``/``TypeError`` from a provider response.
    """

    try:
        key = snapshot.key
        payload = snapshot.payload
        digest = snapshot.digest
    except AttributeError as error:
        raise ConfigContractError(f"{field} is malformed") from error
    return ConfigSnapshot(_revalidate_snapshot_key(key, f"{field} key"), payload, digest)


@dataclass(frozen=True, slots=True)
class ConfigSlice:
    """Nominal base for one resolved, node-owned config projection."""

    snapshot_key: ConfigSnapshotKey

    def __post_init__(self) -> None:
        if type(self.snapshot_key) is not ConfigSnapshotKey:
            raise ConfigContractError("config slice requires a ConfigSnapshotKey")
        _revalidate_snapshot_key(self.snapshot_key, "config slice snapshot key")


class ConfigSelector(Protocol[BoundT_co]):
    """One graph owner's declaration of the narrow config it consumes."""

    def select(self, config: Config, /) -> BoundT_co: ...


def require_config_projection(
    value: ConfigSlice,
    expected: type[ProjectionT],
    field: str,
    /,
) -> ProjectionT:
    """Narrow one erased root slot at its owning domain boundary."""

    if type(value) is not expected:
        raise ConfigContractError(f"{field} is not the declared config projection")
    revalidate_config_slice(value, field)
    return cast(ProjectionT, value)


def revalidate_config_slice(value: ConfigSlice, field: str, /) -> ConfigSlice:
    """Re-run a concrete projection's own invariant checks after decoding."""

    if type(value) is ConfigSlice:
        raise ConfigContractError(f"{field} must be a concrete config projection")
    try:
        value.__post_init__()
    except ConfigContractError:
        raise
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError) as error:
        raise ConfigContractError(f"{field} is malformed") from error
    return value


@dataclass(frozen=True, slots=True)
class Config:
    """Complete resolved config carried through one graph run.

    Fixed ReAct domains have one slot each.  Hook and failover are collections
    because a graph can contain multiple typed slots and Port bindings.  The
    values stay typed ``ConfigSlice`` instances; owner selectors perform the
    only concrete narrowing.
    """

    snapshot: ConfigSnapshot
    react: ConfigSlice
    observe: ConfigSlice
    think: ConfigSlice
    act: ConfigSlice
    hooks: tuple[ConfigSlice, ...]
    failovers: tuple[ConfigSlice, ...] = ()

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ConfigSnapshot:
            raise ConfigContractError("resolved config requires a ConfigSnapshot")
        # Re-run the content check at the resolver boundary.  Normally the
        # dataclass constructor already guarantees this, but persisted values
        # are an adversarial boundary and may have been reconstructed without
        # calling ``ConfigSnapshot.__post_init__``.
        _revalidate_snapshot(self.snapshot, "resolved config snapshot")
        if type(self.hooks) is not tuple:
            raise ConfigContractError("resolved config hooks must be a tuple")
        if type(self.failovers) is not tuple:
            raise ConfigContractError("resolved config failovers must be a tuple")
        projections: tuple[ConfigSlice, ...] = (
            self.react,
            self.observe,
            self.think,
            self.act,
            *self.hooks,
            *self.failovers,
        )
        candidates = cast(tuple[object, ...], projections)
        if any(type(projection) is ConfigSlice or not isinstance(projection, ConfigSlice) for projection in candidates):
            raise ConfigContractError("resolved config domains must be concrete ConfigSlice values")
        for projection in projections:
            revalidate_config_slice(projection, "resolved config projection")
            _revalidate_snapshot_key(projection.snapshot_key, "resolved config slice snapshot key")
            if projection.snapshot_key != self.snapshot.key:
                raise ConfigContractError("every config slice must come from the complete snapshot")

    def bind(self, selector: ConfigSelector[BoundT_co], /) -> BoundT_co:
        """Return exactly the typed projection declared by ``selector``."""

        # ``Config`` can arrive from a deserializer that allocated the frozen
        # dataclass without running ``__post_init__``.  Re-admit the complete
        # aggregate before allowing a selector to inspect any root slot; this
        # keeps a forged aggregate from bypassing the same-snapshot invariant.
        require_config(self)
        try:
            select = selector.select
        except AttributeError as error:
            raise ConfigContractError("Config.bind requires a ConfigSelector") from error
        if not callable(select):
            raise ConfigContractError("Config.bind requires a ConfigSelector")
        selected = select(self)
        if selected is not None:
            if not isinstance(selected, ConfigSlice):
                raise ConfigContractError("ConfigSelector must return a ConfigSlice or None")
            if type(selected) is ConfigSlice:
                raise ConfigContractError("ConfigSelector must return a concrete config projection or None")
            revalidate_config_slice(selected, "selected config projection")
            _revalidate_snapshot_key(selected.snapshot_key, "selected config slice snapshot key")
            if selected.snapshot_key != self.snapshot.key:
                raise ConfigContractError("ConfigSelector returned a projection from a different snapshot")
        return selected

    def admit_state(self, state: GraphRunState, /) -> GraphRunState:
        """Fail closed unless ``state`` references this exact snapshot key."""

        require_config(self)
        state_key = ConfigSnapshotKey.from_state(state)
        if state_key != self.snapshot.key:
            raise ConfigContractError("graph state references a different config snapshot")
        state_digest = _state_config_digest(state)
        if state_digest is not None and state_digest != self.snapshot.digest:
            raise ConfigContractError("graph state config digest does not match the complete snapshot")
        return state

    @property
    def config_cursor(self) -> GraphConfigCursor:
        """Expose this complete Config as the neutral graph-state cursor."""

        return GraphConfigCursor(
            self.snapshot.key.definition_id,
            self.snapshot.key.definition_version,
            self.snapshot.key.revision,
            str(self.snapshot.digest),
        )


@dataclass(frozen=True, slots=True)
class ConfigActivation(Generic[ActivationValueT]):
    """Kernel-internal typed-operation envelope for one activation.

    The typed execution adapter unwraps this envelope before publishing the
    declared domain output.  It therefore carries the complete Config into a
    node (or publishes Observe's successor Config) without placing Config on
    business DTOs or Invocation payloads.
    """

    value: ActivationValueT
    activation_config: Config | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            raise ConfigContractError("activation value is required")
        if self.activation_config is not None:
            require_config(self.activation_config)


def _state_config_digest(state: GraphRunState, /) -> str | None:
    """Read a state digest without making the state layer depend on Config."""

    try:
        digest = state.config_digest
    except AttributeError as error:
        raise ConfigContractError("graph state config digest is malformed") from error
    if digest is None:
        return None
    if type(digest) is not str or not digest or digest != digest.strip():
        raise ConfigContractError("graph state config digest is malformed")
    return digest


def _revalidate_config(config: Config, /) -> Config:
    """Reconstruct a resolved config returned across the resolver boundary."""

    try:
        values = (
            config.snapshot,
            config.react,
            config.observe,
            config.think,
            config.act,
            config.hooks,
            config.failovers,
        )
    except AttributeError as error:
        raise ConfigContractError("resolved config is malformed") from error
    return Config(*values)


def require_config(value: Config, /) -> Config:
    """Admit one complete config aggregate at an assembly or recovery edge.

    Dataclass ``frozen`` instances can still be reconstructed by a persistence
    decoder without invoking ``__post_init__``.  Callers that receive a
    complete config from an external resolver use this function before
    selecting a domain projection.  The original object is returned so node
    assembly never acquires a second config snapshot.
    """

    if type(value) is not Config:
        raise ConfigContractError("config assembly requires an exact Config")
    try:
        Config.__post_init__(value)
    except AttributeError as error:
        raise ConfigContractError("resolved config is malformed") from error
    return value


def _require_snapshot_store(store: object, /) -> ConfigSnapshotStore:
    """Check the two async operations before crossing a persistence Port."""

    if not isinstance(store, ConfigSnapshotStore):
        raise ConfigContractError("config persistence requires a ConfigSnapshotStore")
    if not callable(store.save) or not callable(store.load):
        raise ConfigContractError("config persistence store methods must be callable")
    return store


def _require_config_resolver(resolver: object, /) -> ConfigResolver:
    """Check the resolver capability before invoking an external provider."""

    if not isinstance(resolver, ConfigResolver):
        raise ConfigContractError("config resolution requires a ConfigResolver")
    if not callable(resolver.resolve):
        raise ConfigContractError("config resolver method must be callable")
    return resolver


@runtime_checkable
class ConfigSnapshotStore(Protocol):
    """Append-only persistence Port for complete immutable snapshots.

    ``save`` may be idempotent for an identical value, but must reject a
    different payload for an existing key.  ``load`` resolves only the exact
    key supplied by the caller; this contract intentionally has no latest or
    agent-id lookup operation.
    """

    async def save(self, snapshot: ConfigSnapshot, /) -> ConfigSnapshot: ...

    async def load(self, key: ConfigSnapshotKey, /) -> ConfigSnapshot: ...


@runtime_checkable
class ConfigResolver(Protocol):
    """Resolve runtime capabilities from one already-loaded exact snapshot."""

    async def resolve(self, snapshot: ConfigSnapshot, /) -> Config: ...


async def save_config_snapshot(
    store: ConfigSnapshotStore,
    snapshot: ConfigSnapshot,
    /,
) -> ConfigSnapshot:
    """Persist one revision and require an exact durable confirmation."""

    store = _require_snapshot_store(store)
    if type(snapshot) is not ConfigSnapshot:
        raise ConfigContractError("config persistence requires a ConfigSnapshot")
    snapshot = _revalidate_snapshot(snapshot, "config snapshot")
    confirmed = await store.save(snapshot)
    if type(confirmed) is not ConfigSnapshot:
        raise ConfigContractError("config store did not confirm the exact immutable snapshot")
    confirmed = _revalidate_snapshot(confirmed, "confirmed config snapshot")
    if confirmed != snapshot:
        raise ConfigContractError("config store did not confirm the exact immutable snapshot")
    return confirmed


async def load_config_snapshot(
    store: ConfigSnapshotStore,
    key: ConfigSnapshotKey,
    /,
) -> ConfigSnapshot:
    """Load and admit one exact revision without a latest-version fallback."""

    store = _require_snapshot_store(store)
    if type(key) is not ConfigSnapshotKey:
        raise ConfigContractError("config loading requires a ConfigSnapshotKey")
    key = _revalidate_snapshot_key(key, "config loading key")
    loaded = await store.load(key)
    if type(loaded) is not ConfigSnapshot:
        raise ConfigContractError("config store returned a ConfigSnapshot of the wrong type")
    loaded = _revalidate_snapshot(loaded, "loaded config snapshot")
    if loaded.key != key:
        raise ConfigContractError("config store returned a different snapshot revision")
    return loaded


async def resolve_config(
    resolver: ConfigResolver,
    snapshot: ConfigSnapshot,
    /,
) -> Config:
    """Resolve one snapshot and reject resolver-side revision substitution."""

    resolver = _require_config_resolver(resolver)
    if type(snapshot) is not ConfigSnapshot:
        raise ConfigContractError("config resolution requires a ConfigSnapshot")
    snapshot = _revalidate_snapshot(snapshot, "config snapshot")
    resolved = await resolver.resolve(snapshot)
    if type(resolved) is not Config:
        raise ConfigContractError("config resolver did not preserve the exact snapshot")
    resolved = _revalidate_config(resolved)
    if resolved.snapshot != snapshot:
        raise ConfigContractError("config resolver did not preserve the exact snapshot")
    return resolved


async def recover_config(
    store: ConfigSnapshotStore,
    resolver: ConfigResolver,
    state: GraphRunState,
    /,
) -> Config:
    """Reload and resolve the config named by one authoritative graph state.

    The state identity is converted to an exact ``(definition_id,
    definition_version)`` key before any store call.  There is deliberately no
    latest-config fallback: a recovered graph must be assembled from the same
    immutable snapshot that its state references.
    """

    store = _require_snapshot_store(store)
    resolver = _require_config_resolver(resolver)
    key = ConfigSnapshotKey.from_state(state)
    snapshot = await load_config_snapshot(store, key)
    config = await resolve_config(resolver, snapshot)
    config.admit_state(state)
    return config


__all__ = [
    "Config",
]

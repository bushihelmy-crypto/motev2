"""The explicit Agent session snapshot and its durable codec boundary.

``AgentSession`` is owned by the caller/Agent boundary.  Graph execution does
not infer or merge any of its fields.  The durable envelope below contains
only the encoded hook/context payload and the Config snapshot cursor; runtime
Config capabilities never cross a persistence boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from mote_kernel.config import Config, ConfigContractError, require_config
from mote_kernel.state.graph_state import GraphConfigCursor
from mote_kernel.state.graph_state.identity import is_canonical_identity

HookStateT = TypeVar("HookStateT", covariant=True)
ContextT = TypeVar("ContextT", covariant=True)
CodecHookStateT = TypeVar("CodecHookStateT")
CodecContextT = TypeVar("CodecContextT")
ActivationValueT = TypeVar("ActivationValueT")


class AgentSessionContractError(ValueError):
    """A session or session codec crossed its typed boundary incorrectly."""


class AgentSessionCarrier:
    """Opaque execution carrier for a caller-owned ``AgentSession``.

    Graph infrastructure transports a complete session but must not invent a
    type for the caller's hook state or context.  This base has no state of
    its own; the only concrete value admitted below is the exact
    ``AgentSession`` class.
    """

    __slots__ = ()
    config: Config | None

    @property
    def config_cursor(self) -> GraphConfigCursor | None:
        return self.config.config_cursor if self.config is not None else None

    def admitted(self) -> AgentSessionCarrier:
        raise AgentSessionContractError("execution Session requires an exact AgentSession")


class AgentSessionCodecCarrier:
    """Opaque marker for the caller-provided Session codec capability."""

    __slots__ = ()

    def encode_session(self, session: AgentSessionCarrier, /) -> EncodedAgentSession:
        raise AgentSessionContractError("execution Session codec requires an exact AgentSessionCodec")


@dataclass(frozen=True, slots=True)
class AgentSession(AgentSessionCarrier, Generic[HookStateT, ContextT]):
    """The complete caller-owned snapshot handed to one Agent invocation.

    The three fields are deliberately the only session facts.  ``config`` is
    the resolved in-memory capability; its durable identity is projected by
    :class:`EncodedAgentSession` rather than serializing the capability graph.
    """

    hook_state: HookStateT
    context: ContextT
    config: Config | None = None

    def __post_init__(self) -> None:
        if self.config is not None:
            try:
                require_config(self.config)
            except ConfigContractError as error:
                raise AgentSessionContractError("AgentSession.config is malformed") from error

    @classmethod
    def admit(cls, session: AgentSession[HookStateT, ContextT], /) -> AgentSession[HookStateT, ContextT]:
        if type(session) is not cls:
            raise AgentSessionContractError("AgentSession requires an exact typed snapshot")
        try:
            return cls(session.hook_state, session.context, session.config)
        except (AttributeError, TypeError, ValueError) as error:
            raise AgentSessionContractError("AgentSession is malformed") from error

    def admitted(self) -> AgentSessionCarrier:
        return AgentSession(self.hook_state, self.context, self.config)


def admit_session_carrier(
    session: AgentSessionCarrier,
    /,
) -> AgentSessionCarrier:
    """Reconstruct and admit the one concrete session allowed in execution."""

    if type(session) is not AgentSession:
        raise AgentSessionContractError("execution Session requires an exact AgentSession")
    return session.admitted()


def admit_session_codec_carrier(
    codec: AgentSessionCodecCarrier,
    /,
) -> AgentSessionCodecCarrier:
    """Admit exactly one concrete codec without erasing its caller types."""

    if type(codec) is not AgentSessionCodec:
        raise AgentSessionContractError("execution Session codec requires an exact AgentSessionCodec")
    codec.__post_init__()
    return cast(AgentSessionCodecCarrier, codec)


def encode_session_carrier(
    codec: AgentSessionCodecCarrier,
    session: AgentSessionCarrier,
    /,
) -> EncodedAgentSession:
    """Encode an admitted opaque carrier through its concrete typed codec."""

    admit_session_codec_carrier(codec)
    admit_session_carrier(session)
    return codec.encode_session(session)


@dataclass(frozen=True, slots=True)
class AgentSessionActivation(Generic[ActivationValueT]):
    """A typed node result carrying an explicitly produced session successor.

    The graph never derives a successor from the value.  A node opts in by
    returning this envelope; the execution owner then carries the complete
    snapshot to the same commit boundary as the node result.
    """

    value: ActivationValueT
    session: AgentSessionCarrier

    def __post_init__(self) -> None:
        if self.value is None:
            raise AgentSessionContractError("AgentSession activation value is required")
        try:
            admit_session_carrier(self.session)
        except AgentSessionContractError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise AgentSessionContractError("AgentSession activation session is malformed") from error


@dataclass(frozen=True, slots=True)
class AgentSessionCodec(AgentSessionCodecCarrier, Generic[CodecHookStateT, CodecContextT]):
    """Encode only hook/context values; Config is always stored as a cursor."""

    codec_id: str
    version: int
    encoder: Callable[[CodecHookStateT, CodecContextT], bytes]
    decoder: Callable[[bytes], tuple[CodecHookStateT, CodecContextT]]

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.codec_id):
            raise AgentSessionContractError("AgentSession codec identity must be canonical")
        if type(self.version) is not int or self.version < 1:
            raise AgentSessionContractError("AgentSession codec version must be positive")
        if not callable(self.encoder) or not callable(self.decoder):
            raise AgentSessionContractError("AgentSession codec requires encoder and decoder callables")

    def encode_session(self, session: AgentSessionCarrier, /) -> EncodedAgentSession:
        if type(session) is not AgentSession:
            raise AgentSessionContractError("AgentSession codec requires an exact AgentSession")
        typed_session = cast(AgentSession[CodecHookStateT, CodecContextT], session)
        return self.encode(typed_session)

    def encode(self, session: AgentSession[CodecHookStateT, CodecContextT], /) -> EncodedAgentSession:
        admitted: AgentSession[CodecHookStateT, CodecContextT] = AgentSession[CodecHookStateT, CodecContextT].admit(
            session
        )
        encoded = self._encode_raw(admitted)
        decoded = self._decode_raw(encoded.payload)
        try:
            equivalent = decoded == (admitted.hook_state, admitted.context)
        except Exception as error:
            raise AgentSessionContractError("AgentSession codec could not compare its canonical values") from error
        if not equivalent:
            raise AgentSessionContractError("AgentSession codec is not reversible")
        canonical = self._encode_raw(AgentSession(decoded[0], decoded[1], admitted.config))
        if canonical != encoded:
            raise AgentSessionContractError("AgentSession codec must have a deterministic canonical round trip")
        return encoded

    def _encode_raw(
        self,
        session: AgentSession[CodecHookStateT, CodecContextT],
        /,
    ) -> EncodedAgentSession:
        try:
            payload = self.encoder(session.hook_state, session.context)
        except Exception as error:
            raise AgentSessionContractError("AgentSession encoder rejected the snapshot") from error
        if type(payload) is not bytes:
            raise AgentSessionContractError("AgentSession encoder must return bytes")
        return EncodedAgentSession(self.codec_id, self.version, payload, session.config_cursor)

    def _decode_raw(self, payload: bytes, /) -> tuple[CodecHookStateT, CodecContextT]:
        try:
            decoded = self.decoder(payload)
        except Exception as error:
            raise AgentSessionContractError("AgentSession decoder rejected the durable payload") from error
        if type(decoded) is not tuple or len(decoded) != 2:
            raise AgentSessionContractError("AgentSession decoder must return a two-item tuple")
        return decoded

    def decode(
        self,
        encoded: EncodedAgentSession,
        config: Config | None,
        /,
    ) -> AgentSession[CodecHookStateT, CodecContextT]:
        encoded = EncodedAgentSession.admit(encoded)
        if (encoded.codec_id, encoded.codec_version) != (self.codec_id, self.version):
            raise AgentSessionContractError("AgentSession codec identity/version does not match the durable snapshot")
        if config is not None:
            try:
                require_config(config)
            except ConfigContractError as error:
                raise AgentSessionContractError("resolved AgentSession Config is malformed") from error
        if encoded.config_cursor != (config.config_cursor if config is not None else None):
            raise AgentSessionContractError("durable AgentSession Config cursor does not match resolved Config")
        decoded = self._decode_raw(encoded.payload)
        session = AgentSession(decoded[0], decoded[1], config)
        if self._encode_raw(session) != encoded:
            raise AgentSessionContractError("AgentSession codec must have a deterministic canonical round trip")
        return session


@dataclass(frozen=True, slots=True)
class EncodedAgentSession:
    """Durable session envelope stored beside one Graph state successor."""

    codec_id: str
    codec_version: int
    payload: bytes
    config_cursor: GraphConfigCursor | None = None

    def __post_init__(self) -> None:
        if not is_canonical_identity(self.codec_id):
            raise AgentSessionContractError("durable AgentSession codec identity must be canonical")
        if type(self.codec_version) is not int or self.codec_version < 1:
            raise AgentSessionContractError("durable AgentSession codec version must be positive")
        if type(self.payload) is not bytes:
            raise AgentSessionContractError("durable AgentSession payload must be exact bytes")
        if self.config_cursor is not None:
            try:
                cursor = GraphConfigCursor.admit(self.config_cursor)
            except (TypeError, ValueError) as error:
                raise AgentSessionContractError("durable AgentSession Config cursor is malformed") from error
            if cursor.digest is None:
                raise AgentSessionContractError("durable AgentSession Config cursor requires a digest")

    @classmethod
    def admit(cls, encoded: EncodedAgentSession, /) -> EncodedAgentSession:
        if type(encoded) is not cls:
            raise AgentSessionContractError("durable AgentSession must be an exact encoded envelope")
        try:
            return cls(encoded.codec_id, encoded.codec_version, encoded.payload, encoded.config_cursor)
        except (AttributeError, TypeError, ValueError) as error:
            raise AgentSessionContractError("durable AgentSession envelope is malformed") from error


__all__ = [
    "AgentSession",
    "AgentSessionActivation",
    "AgentSessionCodec",
    "AgentSessionContractError",
    "EncodedAgentSession",
]

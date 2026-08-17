"""Deterministic graph-local resume-frame codec binding."""

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from mote_kernel.execution.graph.values import _GraphValues
from mote_kernel.state.graph_state import GraphResumeInputCodecId

GraphValueT = TypeVar("GraphValueT")
GraphValueT_contra = TypeVar("GraphValueT_contra", contravariant=True)
GraphValueT_co = TypeVar("GraphValueT_co", covariant=True)


class ResumeInputEncoder(Protocol[GraphValueT_contra]):
    def encode(self, value: _GraphValues[GraphValueT_contra]) -> bytes: ...


class ResumeInputDecoder(Protocol[GraphValueT_co]):
    def decode(self, payload: bytes) -> _GraphValues[GraphValueT_co]: ...


@dataclass(frozen=True, slots=True)
class ResumeInputBinding(Generic[GraphValueT]):
    codec_id: GraphResumeInputCodecId
    version: int
    encoder: ResumeInputEncoder[GraphValueT]
    decoder: ResumeInputDecoder[GraphValueT]


__all__: list[str] = []

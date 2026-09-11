from collections.abc import Callable

from mote_kernel.agent import Agent
from mote_kernel.config import Config
from mote_kernel.execution import Graph
from mote_kernel.execution.graph.codec import FrameCodec
from mote_kernel.persistence import AuthorityPort, PersistencePort


def assemble(
    factory: Callable[[Config | None], Graph[str]],
    codec: FrameCodec[str],
    persistence: PersistencePort[int],
    authority: AuthorityPort,
) -> Agent[str]:
    return Agent[str]("agent", factory, codec, persistence, authority)

from diagnostic_ports import LOG_SINK

from mote_kernel.execution import Graph
from mote_kernel.logging import LoggedGraphCommit


async def commit(transition: Graph.Transition[str], /) -> Graph.State:
    return transition.candidate_state


LoggedGraphCommit(inner=commit, sink=LOG_SINK)

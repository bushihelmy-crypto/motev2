from mote_kernel.execution import Graph
from mote_kernel.logging import LoggedGraphCommit
from mote_kernel.logging.record import LogRecord


class Sink:
    def write(self, _record: LogRecord, /) -> None:
        pass


async def commit(transition: Graph.Transition[str], /) -> Graph.State:
    return transition.candidate_state


LoggedGraphCommit[str](Sink())

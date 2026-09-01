from mote_kernel.logging import LoggedNode
from mote_kernel.logging.record import LogRecord


class Sink:
    def write(self, _record: LogRecord, /) -> None:
        pass


async def node(value: str) -> str:
    return value


LoggedNode(node, Sink())

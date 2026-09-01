from mote_kernel.logging import LoggedNode
from mote_kernel.logging.record import LogRecord


class AsyncSink:
    async def write(self, _record: LogRecord, /) -> None:
        pass


LoggedNode(AsyncSink())

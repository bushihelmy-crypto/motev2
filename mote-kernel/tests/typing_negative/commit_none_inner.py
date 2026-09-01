from mote_kernel.logging import LoggedGraphCommit
from mote_kernel.logging.record import LogRecord


class Sink:
    def write(self, _record: LogRecord, /) -> None:
        pass


LoggedGraphCommit(Sink())(None)

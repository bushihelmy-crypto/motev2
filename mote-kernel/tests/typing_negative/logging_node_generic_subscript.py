from diagnostic_ports import LOG_SINK

from mote_kernel.logging import LoggedNode

LoggedNode[str, str](LOG_SINK)

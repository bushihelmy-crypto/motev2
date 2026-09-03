from diagnostic_ports import LOG_SINK

from mote_kernel.logging import LoggedNode


def node(value: str) -> str:
    return value


LoggedNode(LOG_SINK)(node)

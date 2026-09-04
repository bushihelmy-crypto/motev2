from diagnostic_ports import LOG_SINK

from mote_kernel.logging import LoggedNode


async def node(value: str) -> str:
    return value


LoggedNode(node, LOG_SINK)

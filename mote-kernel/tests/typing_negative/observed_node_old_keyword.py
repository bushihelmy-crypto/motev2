from diagnostic_ports import OBSERVABILITY_PORT

from mote_kernel.observability import ObservedNode
from mote_kernel.observability.span import Span, SpanContext, SpanId, TraceId


def span() -> Span:
    return Span(SpanContext(TraceId("trace"), SpanId("span")), "node")


async def node(value: str) -> str:
    return value


ObservedNode(inner=node, port=OBSERVABILITY_PORT, span_factory=span)

from mote_kernel.observability import ObservedNode
from mote_kernel.observability.span import Span, SpanContext, SpanId, TraceId


def span() -> Span:
    return Span(SpanContext(TraceId("trace"), SpanId("span")), "node")


ObservedNode(None, span)

from mote_kernel.observability import ObservedNode
from mote_kernel.observability.record import Observation
from mote_kernel.observability.span import Span, SpanContext, SpanId, TraceId


class Port:
    def record(self, _observation: Observation, /) -> None:
        pass


def span() -> Span:
    return Span(SpanContext(TraceId("trace"), SpanId("span")), "node")


async def node(value: str) -> str:
    return value


ObservedNode(inner=node, port=Port(), span_factory=span)

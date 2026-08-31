"""嵌套批量审批：同一个子图可复用，并按 scope 一次恢复多个中断。

The parent owns two activations of one child definition.  Both children use
the local node id ``review``; the ``scope`` on each resume action is what keeps
their state and interrupt identities separate.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum

from mote_kernel.execution import Graph


class ReviewDecision(Enum):
    PENDING = "pending"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True)
class ReviewPacket:
    document: str
    decision: ReviewDecision
    note: str


_SEPARATOR = "\x1f"


def encode_review_packet(values: Graph.Values[ReviewPacket]) -> bytes:
    packet = values["packet"]
    fields = (packet.document, packet.decision.value, packet.note)
    if any(_SEPARATOR in field for field in fields):
        raise ValueError("review packet fields cannot contain the resume separator")
    return _SEPARATOR.join(fields).encode("utf-8")


def decode_review_packet(payload: bytes) -> Graph.Values[ReviewPacket]:
    fields = payload.decode("utf-8").split(_SEPARATOR)
    if len(fields) != 3:
        raise ValueError("review packet payload must contain three fields")
    try:
        decision = ReviewDecision(fields[1])
    except ValueError as error:
        raise ValueError("review packet payload has an unknown decision") from error
    return Graph.values(packet=ReviewPacket(fields[0], decision, fields[2]))


async def review(values: Graph.Values[ReviewPacket]) -> Graph.Outcome[ReviewPacket]:
    """Interrupt while pending and publish the reviewer decision otherwise."""

    packet = values["packet"]
    if packet.decision is ReviewDecision.PENDING:
        return Graph.interrupt(f"请审批：{packet.document}".encode())
    return Graph.success(Graph.values(packet=packet))


def build_review_child() -> Graph[ReviewPacket]:
    child = Graph[ReviewPacket]("example.nested-batch-review.child")
    child.set_resume_codec("review-packet", 1, encode_review_packet, decode_review_packet)
    child.add_node(
        "review",
        review,
        inputs={"packet": Graph.graph_input("packet", ReviewPacket)},
        outputs={"packet": ReviewPacket},
    )
    child.set_outputs({"packet": Graph.node_output("review", "packet")})
    return child


async def combine_reviews(values: Graph.Values[ReviewPacket]) -> Graph.Values[ReviewPacket]:
    legal = values["legal"]
    safety = values["safety"]
    if legal.document != safety.document:
        raise ValueError("reviewers must inspect the same document")
    return Graph.values(
        packet=ReviewPacket(
            legal.document,
            ReviewDecision.APPROVED,
            f"{legal.note}；{safety.note}",
        )
    )


def build_graph() -> Graph[ReviewPacket]:
    """Build two scoped activations from one reusable child graph definition."""

    child = build_review_child()
    graph = Graph[ReviewPacket]("example.nested-batch-review")
    packet = Graph.graph_input("packet", ReviewPacket)
    graph.add_node("legal", child, inputs={"packet": packet})
    graph.add_node("safety", child, inputs={"packet": packet})
    graph.add_node(
        "combine",
        combine_reviews,
        inputs={
            "legal": Graph.node_output("legal", "packet"),
            "safety": Graph.node_output("safety", "packet"),
        },
        outputs={"packet": ReviewPacket},
    )
    graph.add_join(("legal", "safety"), "combine")
    graph.set_outputs({"packet": Graph.node_output("combine", "packet")})
    return graph


async def main() -> None:
    document = (await asyncio.to_thread(input, "待审批文档：")).strip()
    pending = ReviewPacket(document, ReviewDecision.PENDING, "")
    graph = build_graph()
    paused = await graph.run(Graph.values(packet=pending), run_id="batch-review")
    if not isinstance(paused, Graph.AwaitingResumeResult):
        print("审批没有进入等待状态。")
        return

    actions = tuple(
        graph.resume_interrupted(
            "review",
            interrupt.interrupt_id,
            Graph.values(packet=ReviewPacket(document, ReviewDecision.APPROVED, "已通过")),
            scope=interrupt.scope,
        )
        for interrupt in paused.interrupts
    )
    completed = await graph.run(
        state=paused.state,
        continuation=paused.continuation,
        resume=actions,
    )
    if isinstance(completed, Graph.CompletedResult):
        print(f"批量审批完成：{completed.outputs['packet'].note}")
    else:
        print("审批仍未完成。")


if __name__ == "__main__":
    asyncio.run(main())

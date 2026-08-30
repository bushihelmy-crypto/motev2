"""这是一个可恢复的人工审批图：审批决定完全来自显式输入，不依赖进程内计数器。"""

import asyncio
from dataclasses import dataclass
from enum import Enum

from mote_kernel.execution import Graph


class ReviewStatus(Enum):
    PENDING = 0
    APPROVED = 1


@dataclass(frozen=True, slots=True)
class Article:
    title: str
    review: ReviewStatus


def encode_article(values: Graph.Values[Article]) -> bytes:
    article = values["article"]
    marker = b"\x00" if article.review is ReviewStatus.PENDING else b"\x01"
    return marker + article.title.encode("utf-8")


def decode_article(payload: bytes) -> Graph.Values[Article]:
    marker = payload[:1]
    if marker == b"\x00":
        review = ReviewStatus.PENDING
    elif marker == b"\x01":
        review = ReviewStatus.APPROVED
    else:
        raise ValueError("article payload has an unknown review status")
    return Graph.values(article=Article(payload[1:].decode("utf-8"), review))


async def publish_article(values: Graph.Values[Article]) -> Graph.Outcome[Article]:
    article = values["article"]
    if article.review is ReviewStatus.PENDING:
        return Graph.interrupt(article.title.encode("utf-8"))
    return Graph.success(Graph.values(published=article))


def build_graph() -> Graph[Article]:
    graph = Graph[Article]("example.human-in-the-loop")
    graph.set_resume_codec("article", 1, encode_article, decode_article)
    graph.add_node(
        "publish",
        publish_article,
        inputs={"article": Graph.graph_input("article", Article)},
        outputs={"published": Article},
    )
    graph.set_outputs({"published": Graph.node_output("publish", "published")})
    return graph


async def main() -> None:
    title = (await asyncio.to_thread(input, "请输入待发布的标题：")).strip()
    pending = Article(title, ReviewStatus.PENDING)
    paused = await build_graph().run(Graph.values(article=pending), run_id="editorial")
    if not isinstance(paused, Graph.AwaitingResumeResult):
        print("稿件没有进入人工审批。")
        return
    interrupt = paused.interrupts[0]
    print(f"收到审批请求：{interrupt.request_payload.decode('utf-8')}")
    approved_title = (await asyncio.to_thread(input, "请输入审批后的标题（直接回车表示不修改）：")).strip() or title

    recovered_graph = build_graph()
    completed = await recovered_graph.run(
        state=paused.state,
        resume=(
            recovered_graph.resume_interrupted(
                "publish",
                interrupt.interrupt_id,
                Graph.values(article=Article(approved_title, ReviewStatus.APPROVED)),
            ),
        ),
    )
    if isinstance(completed, Graph.CompletedResult):
        print(f"已发布：{completed.outputs['published'].title}")
    else:
        print("稿件仍未发布。")


if __name__ == "__main__":
    asyncio.run(main())

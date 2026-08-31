"""失败节点的纯跳过与路由：不需要伪造失败节点输出也能继续。

When moderation fails, an operator can select the ``manual`` route.  The
manual and automatic branches read the original graph input, so the action
uses ``skip_failed`` without an output substitution.  This is different from
``skip_failed_delivery``, where a downstream node needs an injected output.
"""

import asyncio

from mote_kernel.execution import Graph


async def moderate(values: Graph.Values[str]) -> Graph.Outcome[str]:
    text = values["text"]
    if text.startswith("blocked:"):
        return Graph.failure("moderation service unavailable")
    return Graph.success(Graph.values(), route="automatic")


async def automatic_review(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(text=f"自动审核：{values['text']}")


async def manual_review(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(text=f"人工审核：{values['text']}")


async def finish(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(result=f"已处理：{values['text']}")


def build_graph() -> Graph[str]:
    graph = Graph[str]("example.skip-failed-route")
    text = Graph.graph_input("text", str)
    graph.add_node("moderate", moderate, inputs={"text": text}, outputs={})
    graph.add_node("automatic", automatic_review, inputs={"text": text}, outputs={"text": str})
    graph.add_node("manual", manual_review, inputs={"text": text}, outputs={"text": str})
    graph.add_node("finish", finish, inputs={"text": text}, outputs={"result": str})
    graph.add_conditional_edge("moderate", "automatic", "automatic")
    graph.add_conditional_edge("moderate", "manual", "manual")
    graph.add_edge("automatic", "finish")
    graph.add_edge("manual", "finish")
    graph.add_edge("finish", Graph.END)
    graph.set_outputs({"result": Graph.node_output("finish", "result")})
    return graph


async def main() -> None:
    text = (await asyncio.to_thread(input, "文本（以 blocked: 开头模拟服务失败）：")).strip()
    graph = build_graph()
    first = await graph.run(Graph.values(text=text), run_id="moderation")
    if isinstance(first, Graph.AwaitingResumeResult):
        recovered = await graph.run(
            state=first.state,
            continuation=first.continuation,
            resume=(graph.skip_failed("moderate", "operator selected manual review", route="manual"),),
        )
    else:
        recovered = first
    if isinstance(recovered, Graph.CompletedResult):
        print(recovered.outputs["result"])
    else:
        print("审核仍未完成。")


if __name__ == "__main__":
    asyncio.run(main())

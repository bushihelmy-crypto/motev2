"""执行预算示例：用 superstep 上限保护长流程，并在预算足够时复用同一张图。

Execution limits are operational guardrails rather than node business logic.
The first invocation intentionally fails closed; the caller can choose a
larger budget and start a new run without changing the graph definition.
"""

import asyncio

from mote_kernel.execution import Graph


async def collect_step(values: Graph.Values[str]) -> Graph.Values[str]:
    """Append one deterministic stage marker."""

    return Graph.values(report=f"{values['report']} -> stage")


def build_graph() -> Graph[str]:
    """Build a three-stage pipeline that needs more than one superstep."""

    graph = Graph[str]("example.bounded-execution")
    graph.add_node(
        "extract",
        collect_step,
        inputs={"report": Graph.graph_input("report", str)},
        outputs={"report": str},
    )
    graph.add_node(
        "transform",
        collect_step,
        inputs={"report": Graph.node_output("extract", "report")},
        outputs={"report": str},
    )
    graph.add_node(
        "publish",
        collect_step,
        inputs={"report": Graph.node_output("transform", "report")},
        outputs={"report": str},
    )
    graph.add_edge("extract", "transform")
    graph.add_edge("transform", "publish")
    graph.add_edge("publish", Graph.END)
    graph.set_outputs({"report": Graph.node_output("publish", "report")})
    return graph


async def main() -> None:
    graph = build_graph()
    try:
        await graph.run(
            Graph.values(report="daily report"),
            run_id="bounded-report-too-small",
            max_supersteps=1,
        )
    except Graph.ExecutionLimitError as error:
        print(f"预算不足，安全停止：{error}")

    result = await graph.run(
        Graph.values(report="daily report"),
        run_id="bounded-report",
        max_supersteps=3,
    )
    if isinstance(result, Graph.CompletedResult):
        print(result.outputs["report"])
    else:
        print("报告尚未完成。")


if __name__ == "__main__":
    asyncio.run(main())

"""这是一个扇出再汇合图：三位侦探并行调查，全部完成后由推理节点汇总证词。"""

import asyncio

from mote_kernel.execution import Graph


async def inspect_window(values: Graph.Values[str]) -> Graph.Values[str]:
    await asyncio.sleep(0)
    return Graph.values(window=f"{values['case']}：窗台有湿泥")


async def inspect_clock(values: Graph.Values[str]) -> Graph.Values[str]:
    await asyncio.sleep(0)
    return Graph.values(clock=f"{values['case']}：时钟停在午夜")


async def interview_cat(values: Graph.Values[str]) -> Graph.Values[str]:
    await asyncio.sleep(0)
    return Graph.values(cat=f"{values['case']}：猫只对园丁哈气")


async def deduce(values: Graph.Values[str]) -> Graph.Values[str]:
    evidence = "；".join((values["window"], values["clock"], values["cat"]))
    return Graph.values(verdict=f"结论：园丁来过。证据：{evidence}")


def build_graph() -> Graph[str]:
    case = Graph.graph_input("case", str)
    graph = Graph[str]("example.parallel-detectives")
    graph.add_node("window", inspect_window, inputs={"case": case}, outputs={"window": str})
    graph.add_node("clock", inspect_clock, inputs={"case": case}, outputs={"clock": str})
    graph.add_node("cat", interview_cat, inputs={"case": case}, outputs={"cat": str})
    graph.add_node(
        "deduce",
        deduce,
        inputs={
            "window": Graph.node_output("window", "window"),
            "clock": Graph.node_output("clock", "clock"),
            "cat": Graph.node_output("cat", "cat"),
        },
        outputs={"verdict": str},
    )
    graph.add_join(("window", "clock", "cat"), "deduce")
    graph.set_outputs({"verdict": Graph.node_output("deduce", "verdict")})
    return graph


async def main() -> None:
    case = (await asyncio.to_thread(input, "请给这宗案件起个名字：")).strip()
    result = await build_graph().run(Graph.values(case=case), run_id="detectives")
    if isinstance(result, Graph.CompletedResult):
        print(result.outputs["verdict"])
    else:
        print("侦探们还没有完成调查。")


if __name__ == "__main__":
    asyncio.run(main())

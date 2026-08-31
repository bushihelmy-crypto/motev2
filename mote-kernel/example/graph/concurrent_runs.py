"""同一张已编译 graph 并行承载多个独立 run。

The graph definition is immutable after compilation, while each invocation
gets its own run identity and state.  Callers can therefore share one graph
instance across requests; omit ``run_id`` when the facade should generate one.
"""

import asyncio

from mote_kernel.execution import Graph


async def personalize(values: Graph.Values[str]) -> Graph.Values[str]:
    await asyncio.sleep(0)
    return Graph.values(greeting=f"你好，{values['name']}！")


def build_graph() -> Graph[str]:
    graph = Graph[str]("example.concurrent-runs")
    graph.add_node(
        "personalize",
        personalize,
        inputs={"name": Graph.graph_input("name", str)},
        outputs={"greeting": str},
    )
    graph.set_outputs({"greeting": Graph.node_output("personalize", "greeting")})
    return graph


async def main() -> None:
    graph = build_graph()
    first, second = await asyncio.gather(
        graph.run(Graph.values(name="小明")),
        graph.run(Graph.values(name="小红")),
    )
    if isinstance(first, Graph.CompletedResult) and isinstance(second, Graph.CompletedResult):
        print(first.outputs["greeting"])
        print(second.outputs["greeting"])
    else:
        print("至少一个请求尚未完成。")


if __name__ == "__main__":
    asyncio.run(main())

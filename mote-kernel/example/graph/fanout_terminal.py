"""直接扇出并在 Join 后结束：graph output 也可以直接来自 graph input。

The preparation result is sent to two independent checks.  ``add_join`` can
target ``Graph.END`` when no post-join node is needed; the output projection
can still expose both a renamed input and a node publication.
"""

import asyncio

from mote_kernel.execution import Graph


async def prepare(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(token=f"token:{values['request']}")


async def audit(values: Graph.Values[str]) -> Graph.Values[str]:
    if not values["token"].startswith("token:"):
        raise ValueError("audit received an invalid token")
    await asyncio.sleep(0)
    return Graph.values()


async def warm_cache(values: Graph.Values[str]) -> Graph.Values[str]:
    if not values["token"].startswith("token:"):
        raise ValueError("cache received an invalid token")
    await asyncio.sleep(0)
    return Graph.values()


def build_graph() -> Graph[str]:
    graph = Graph[str]("example.fanout-terminal")
    request = Graph.graph_input("request", str)
    graph.add_node("prepare", prepare, inputs={"request": request}, outputs={"token": str})
    graph.add_node(
        "audit",
        audit,
        inputs={"token": Graph.node_output("prepare", "token")},
        outputs={},
    )
    graph.add_node(
        "cache",
        warm_cache,
        inputs={"token": Graph.node_output("prepare", "token")},
        outputs={},
    )
    graph.add_edge("prepare", "audit")
    graph.add_edge("prepare", "cache")
    graph.add_join(("audit", "cache"), Graph.END)
    graph.set_outputs(
        {
            "original_request": request,
            "prepared_token": Graph.node_output("prepare", "token"),
        }
    )
    return graph


async def main() -> None:
    request = (await asyncio.to_thread(input, "请求名：")).strip()
    result = await build_graph().run(Graph.values(request=request), run_id="fanout-terminal", max_parallel_tasks=2)
    if isinstance(result, Graph.CompletedResult):
        print(f"{result.outputs['original_request']} -> {result.outputs['prepared_token']}")
    else:
        print("请求尚未完成。")


if __name__ == "__main__":
    asyncio.run(main())

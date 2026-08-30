"""这是一个线性寻宝图：解码线索 -> 定位宝藏 -> 打开宝箱。"""

import asyncio

from mote_kernel.execution import Graph


async def decode_clue(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(location=values["clue"].replace("倒着读：", "")[::-1])


async def locate_treasure(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(chest=f"{values['location']}的蓝色宝箱")


async def open_chest(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(treasure=f"打开{values['chest']}，得到一枚星星金币")


def build_graph() -> Graph[str]:
    graph = Graph[str]("example.linear-treasure-hunt")
    graph.add_node(
        "decode",
        decode_clue,
        inputs={"clue": Graph.graph_input("clue", str)},
        outputs={"location": str},
    )
    graph.add_node(
        "locate",
        locate_treasure,
        inputs={"location": Graph.node_output("decode", "location")},
        outputs={"chest": str},
    )
    graph.add_node(
        "open",
        open_chest,
        inputs={"chest": Graph.node_output("locate", "chest")},
        outputs={"treasure": str},
    )
    graph.add_edge("decode", "locate")
    graph.add_edge("locate", "open")
    graph.set_outputs({"treasure": Graph.node_output("open", "treasure")})
    return graph


async def main() -> None:
    clue = (await asyncio.to_thread(input, "请输入一条倒序线索（例如：倒着读：洞树老）：")).strip()
    result = await build_graph().run(Graph.values(clue=clue), run_id="treasure-hunt")
    if isinstance(result, Graph.CompletedResult):
        print(result.outputs["treasure"])
    else:
        print("寻宝尚未完成。")


if __name__ == "__main__":
    asyncio.run(main())

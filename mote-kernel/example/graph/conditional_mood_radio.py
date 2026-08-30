"""这是一个条件路由图：心情 DJ 只准备一条歌单路径，再进入统一播放节点。"""

import asyncio

from mote_kernel.execution import Graph


async def choose_mood(values: Graph.Values[str]) -> Graph.Outcome[str]:
    mood = values["mood"]
    if mood in {"开心", "兴奋"}:
        return Graph.success(Graph.values(playlist="霓虹派对歌单"), route="party")
    return Graph.success(Graph.values(playlist="雨夜钢琴歌单"), route="quiet")


async def prepare_party_playlist(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(prepared=f"派对灯光已配合{values['playlist']}完成准备")


async def prepare_quiet_playlist(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(prepared=f"环境音量已配合{values['playlist']}完成准备")


async def start_playback(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(message=f"正在播放{values['playlist']}")


def build_graph() -> Graph[str]:
    graph = Graph[str]("example.conditional-mood-radio")
    graph.add_node(
        "dj",
        choose_mood,
        inputs={"mood": Graph.graph_input("mood", str)},
        outputs={"playlist": str},
    )
    graph.add_node(
        "party-playlist",
        prepare_party_playlist,
        inputs={"playlist": Graph.node_output("dj", "playlist")},
        outputs={"prepared": str},
    )
    graph.add_node(
        "quiet-playlist",
        prepare_quiet_playlist,
        inputs={"playlist": Graph.node_output("dj", "playlist")},
        outputs={"prepared": str},
    )
    graph.add_node(
        "playback",
        start_playback,
        inputs={"playlist": Graph.node_output("dj", "playlist")},
        outputs={"message": str},
    )
    graph.add_conditional_edge("dj", "party", "party-playlist")
    graph.add_conditional_edge("dj", "quiet", "quiet-playlist")
    graph.add_edge("party-playlist", "playback")
    graph.add_edge("quiet-playlist", "playback")
    graph.set_outputs({"message": Graph.node_output("playback", "message")})
    return graph


async def main() -> None:
    mood = (await asyncio.to_thread(input, "你现在是什么心情？")).strip()
    result = await build_graph().run(Graph.values(mood=mood), run_id="mood-radio")
    if isinstance(result, Graph.CompletedResult):
        print(result.outputs["message"])
    else:
        print("DJ 暂时没有完成选歌。")


if __name__ == "__main__":
    asyncio.run(main())

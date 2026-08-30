"""这是一个嵌套图：母图执行太空任务，子图封装“点火 -> 入轨”的发射流程。"""

import asyncio

from mote_kernel.execution import Graph


async def ignite(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(rocket=f"{values['ship']}主引擎已点火")


async def enter_orbit(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(orbit=f"{values['rocket']}，稳定进入月球轨道")


async def deploy_probe(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(report=f"{values['orbit']}；探测器开始扫描冰川")


def build_launch_graph() -> Graph[str]:
    launch = Graph[str]("example.nested-space-mission.launch")
    launch.add_node(
        "ignite",
        ignite,
        inputs={"ship": Graph.graph_input("ship", str)},
        outputs={"rocket": str},
    )
    launch.add_node(
        "orbit",
        enter_orbit,
        inputs={"rocket": Graph.node_output("ignite", "rocket")},
        outputs={"orbit": str},
    )
    launch.add_edge("ignite", "orbit")
    launch.set_outputs({"orbit": Graph.node_output("orbit", "orbit")})
    return launch


def build_graph() -> Graph[str]:
    mission = Graph[str]("example.nested-space-mission")
    mission.add_node("launch", build_launch_graph(), inputs={"ship": Graph.graph_input("ship", str)})
    mission.add_node(
        "probe",
        deploy_probe,
        inputs={"orbit": Graph.node_output("launch", "orbit")},
        outputs={"report": str},
    )
    mission.add_edge("launch", "probe")
    mission.set_outputs({"report": Graph.node_output("probe", "report")})
    return mission


async def main() -> None:
    ship = (await asyncio.to_thread(input, "请为飞船命名：")).strip()
    result = await build_graph().run(Graph.values(ship=ship), run_id="space-mission")
    if isinstance(result, Graph.CompletedResult):
        print(result.outputs["report"])
    else:
        print("太空任务尚未完成。")


if __name__ == "__main__":
    asyncio.run(main())

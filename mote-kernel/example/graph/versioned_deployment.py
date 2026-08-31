"""定义版本与 state：拓扑变更时拒绝旧快照并显式启动新 run。

Definition identity and version are part of the authoritative snapshot.  A
new topology must not silently consume a state produced by the old one; the
caller catches ``SnapshotMismatchError`` and chooses an explicit migration or
fresh run.
"""

import asyncio

from mote_kernel.execution import Graph


async def publish(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(message=f"v{values['version']}:{values['payload']}")


def build_graph(version: int) -> Graph[str]:
    graph = Graph[str]("example.versioned-deployment", version=version)
    graph.add_node(
        "publish",
        publish,
        inputs={
            "version": Graph.graph_input("version", str),
            "payload": Graph.graph_input("payload", str),
        },
        outputs={"message": str},
    )
    graph.set_outputs({"message": Graph.node_output("publish", "message")})
    return graph


async def main() -> None:
    old = await build_graph(1).run(
        Graph.values(version="1", payload="legacy"),
        run_id="versioned-run",
    )
    if not isinstance(old, Graph.CompletedResult):
        print("旧版本运行未完成。")
        return

    new_graph = build_graph(2)
    try:
        await new_graph.run(state=old.state)
    except Graph.SnapshotMismatchError as error:
        print(f"拒绝旧快照：{error}")

    restarted = await new_graph.run(
        Graph.values(version="2", payload="migrated"),
        run_id="versioned-run-v2",
    )
    if isinstance(restarted, Graph.CompletedResult):
        print(restarted.outputs["message"])


if __name__ == "__main__":
    asyncio.run(main())

"""客户报告图：并行读取多个来源，同时限制共享数据库资源。

The profile and order readers both require the exclusive ``customer-db``
resource.  The preferences reader does not, so it can run alongside whichever
database reader currently owns the resource.  A join waits for all sections
before publishing one report.
"""

import asyncio
from dataclasses import dataclass

from mote_kernel.execution import Graph


@dataclass(frozen=True, slots=True)
class CustomerSnapshot:
    customer_id: str
    content: str


async def read_profile(values: Graph.Values[CustomerSnapshot]) -> Graph.Values[CustomerSnapshot]:
    """Read the profile section under the shared customer database lease."""

    await asyncio.sleep(0)
    request = values["request"]
    return Graph.values(profile=CustomerSnapshot(request.customer_id, f"客户 {request.customer_id}：高级会员"))


async def read_orders(values: Graph.Values[CustomerSnapshot]) -> Graph.Values[CustomerSnapshot]:
    """Read order history under the same exclusive lease as the profile."""

    await asyncio.sleep(0)
    request = values["request"]
    return Graph.values(orders=CustomerSnapshot(request.customer_id, "最近订单：3 笔，均已完成"))


async def read_preferences(values: Graph.Values[CustomerSnapshot]) -> Graph.Values[CustomerSnapshot]:
    """Read a cache-backed section that does not consume the database lease."""

    await asyncio.sleep(0)
    request = values["request"]
    return Graph.values(preferences=CustomerSnapshot(request.customer_id, "偏好：环保包装"))


async def assemble_report(values: Graph.Values[CustomerSnapshot]) -> Graph.Values[CustomerSnapshot]:
    """Join the independently materialized sections into one report."""

    profile = values["profile"]
    orders = values["orders"]
    preferences = values["preferences"]
    if not (profile.customer_id == orders.customer_id == preferences.customer_id):
        raise ValueError("customer report sections refer to different customers")
    content = "；".join((profile.content, orders.content, preferences.content))
    return Graph.values(report=CustomerSnapshot(profile.customer_id, content))


def build_graph() -> Graph[CustomerSnapshot]:
    """Build a report graph with a shared resource and an explicit join."""

    graph = Graph[CustomerSnapshot]("example.resource-customer-report")
    request = Graph.graph_input("request", CustomerSnapshot)
    graph.add_node(
        "profile",
        read_profile,
        inputs={"request": request},
        outputs={"profile": CustomerSnapshot},
        resources=("customer-db",),
    )
    graph.add_node(
        "orders",
        read_orders,
        inputs={"request": request},
        outputs={"orders": CustomerSnapshot},
        resources=("customer-db",),
    )
    graph.add_node(
        "preferences",
        read_preferences,
        inputs={"request": request},
        outputs={"preferences": CustomerSnapshot},
    )
    graph.add_node(
        "assemble",
        assemble_report,
        inputs={
            "profile": Graph.node_output("profile", "profile"),
            "orders": Graph.node_output("orders", "orders"),
            "preferences": Graph.node_output("preferences", "preferences"),
        },
        outputs={"report": CustomerSnapshot},
    )
    graph.add_join(("profile", "orders", "preferences"), "assemble")
    graph.set_outputs({"report": Graph.node_output("assemble", "report")})
    return graph


async def main() -> None:
    customer_id = (await asyncio.to_thread(input, "客户 ID：")).strip()
    request = CustomerSnapshot(customer_id, "")
    result = await build_graph().run(
        Graph.values(request=request),
        run_id="customer-report",
        max_parallel_tasks=3,
    )
    if isinstance(result, Graph.CompletedResult):
        print(result.outputs["report"].content)
    else:
        print("客户报告尚未完成。")


if __name__ == "__main__":
    asyncio.run(main())

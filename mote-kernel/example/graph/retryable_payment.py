"""可恢复的支付流程：失败节点可在新进程中用修正后的输入重试。

The payment provider is modelled as a pure node so the example is deterministic.
Use ``resume_failed_with`` when an operator has a corrected payment token; the
resume codec is the only thing needed to carry that replacement across a
process boundary.
"""

import asyncio
from dataclasses import dataclass, replace
from enum import Enum

from mote_kernel.execution import Graph


class OrderStatus(Enum):
    NEW = "new"
    PAID = "paid"
    SHIPPED = "shipped"


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    payment_token: str
    status: OrderStatus


_SEPARATOR = "\x1f"


def encode_order(values: Graph.Values[Order]) -> bytes:
    """Encode the replacement input with a small, deterministic wire format."""

    order = values["order"]
    fields = (order.order_id, order.payment_token, order.status.value)
    if any(_SEPARATOR in field for field in fields):
        raise ValueError("order fields cannot contain the resume separator")
    return _SEPARATOR.join(fields).encode("utf-8")


def decode_order(payload: bytes) -> Graph.Values[Order]:
    """Decode one replacement input and reject malformed recovery data."""

    fields = payload.decode("utf-8").split(_SEPARATOR)
    if len(fields) != 3:
        raise ValueError("order resume payload must contain three fields")
    try:
        status = OrderStatus(fields[2])
    except ValueError as error:
        raise ValueError("order resume payload has an unknown status") from error
    return Graph.values(order=Order(fields[0], fields[1], status))


async def authorize_payment(values: Graph.Values[Order]) -> Graph.Outcome[Order]:
    """Return a typed failure for a declined token, otherwise mark the order paid."""

    order = values["order"]
    if not order.payment_token or order.payment_token == "declined":
        return Graph.failure("payment provider declined the token")
    return Graph.success(Graph.values(order=replace(order, status=OrderStatus.PAID)))


async def ship_order(values: Graph.Values[Order]) -> Graph.Values[Order]:
    """Ship only the materialized result of the authorization node."""

    return Graph.values(order=replace(values["order"], status=OrderStatus.SHIPPED))


def build_graph() -> Graph[Order]:
    """Build a fresh immutable order graph for each invocation or recovery."""

    graph = Graph[Order]("example.retryable-payment")
    graph.set_resume_codec("order", 1, encode_order, decode_order)
    graph.add_node(
        "authorize",
        authorize_payment,
        inputs={"order": Graph.graph_input("order", Order)},
        outputs={"order": Order},
    )
    graph.add_node(
        "ship",
        ship_order,
        inputs={"order": Graph.node_output("authorize", "order")},
        outputs={"order": Order},
    )
    graph.add_edge("authorize", "ship")
    graph.add_edge("ship", Graph.END)
    graph.set_outputs({"order": Graph.node_output("ship", "order")})
    return graph


async def main() -> None:
    order_id = (await asyncio.to_thread(input, "订单号：")).strip()
    token = (await asyncio.to_thread(input, "支付 token（输入 declined 模拟失败）：")).strip()
    order = Order(order_id, token, OrderStatus.NEW)

    graph = build_graph()
    first = await graph.run(Graph.values(order=order), run_id="payment-order")
    if isinstance(first, Graph.CompletedResult):
        print(f"订单已完成：{first.outputs['order'].status.value}")
        return
    if not isinstance(first, Graph.AwaitingResumeResult):
        print("订单流程未能进入可恢复状态。")
        return

    failure = first.failures[0]
    print(f"节点 {failure.node_id} 失败：{failure.failure}")
    replacement_token = (await asyncio.to_thread(input, "请输入新的支付 token：")).strip()
    replacement = Order(order_id, replacement_token, OrderStatus.NEW)

    # 模拟 worker 重启: 只传 authoritative state, 不传旧 graph 实例或本地计数器.
    recovered_graph = build_graph()
    recovered = await recovered_graph.run(
        state=first.state,
        resume=(
            recovered_graph.resume_failed_with(
                "authorize",
                Graph.values(order=replacement),
            ),
        ),
    )
    if isinstance(recovered, Graph.CompletedResult):
        print(f"重试后订单已完成：{recovered.outputs['order'].status.value}")
    else:
        print("重试仍未完成，请保留 state 继续处理。")


if __name__ == "__main__":
    asyncio.run(main())

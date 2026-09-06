"""Strict positive typing example for one compiled typed node contract."""

from dataclasses import dataclass
from typing import TypeAlias, assert_type

from mote_kernel.execution import Graph


@dataclass(frozen=True, slots=True)
class Left:
    value: str


@dataclass(frozen=True, slots=True)
class Right:
    value: int


@dataclass(frozen=True, slots=True)
class Request:
    left: Left
    right: Right


@dataclass(frozen=True, slots=True)
class Result:
    value: str


Value: TypeAlias = Left | Right | Request | Result


async def combine(request: Request) -> Result:
    return Result(f"{request.left.value}:{request.right.value}")


graph = Graph[Value]("typing.typed-node")
left = Graph.bind("left", Graph.graph_input("left", Left))
right = Graph.bind("right", Graph.graph_input("right", Right))
output = graph.add_node(
    "combine",
    combine,
    inputs=(left, right),
    input_type=Request,
    materialize=lambda values: Request(values.get(left), values.get(right)),
    output_name="result",
    output_type=Result,
)

assert_type(left, Graph.InputBinding[Left])
assert_type(right, Graph.InputBinding[Right])
assert_type(output, Graph.OutputRef[Result])

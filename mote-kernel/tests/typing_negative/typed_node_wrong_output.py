from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.execution import Graph


@dataclass(frozen=True)
class Request:
    value: str


@dataclass(frozen=True)
class Result:
    value: str


@dataclass(frozen=True)
class WrongResult:
    value: str


Value: TypeAlias = Request | Result | WrongResult


async def operation(_request: Request) -> WrongResult:
    return WrongResult("wrong")


graph = Graph[Value]("typing.typed-wrong-output")
request = Graph.bind("request", Graph.graph_input("request", Request))
output: Graph.OutputRef[Result] = graph.add_node(
    "node",
    operation,
    inputs=(request,),
    input_type=Request,
    materialize=lambda values: values.get(request),
    output_name="result",
    output_type=Result,
)

from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.execution import Graph


@dataclass(frozen=True)
class Source:
    value: str


@dataclass(frozen=True)
class Request:
    value: str


@dataclass(frozen=True)
class Result:
    value: str


Value: TypeAlias = Source | Request | Result


async def operation(_request: Request) -> Result:
    return Result("result")


graph = Graph[Value]("typing.typed-wrong-input")
source = Graph.bind("source", Graph.graph_input("source", Source))
graph.add_node(
    "node",
    operation,
    inputs=(source,),
    input_type=Request,
    materialize=lambda values: values.get(source),
    output_name="result",
    output_type=Result,
)

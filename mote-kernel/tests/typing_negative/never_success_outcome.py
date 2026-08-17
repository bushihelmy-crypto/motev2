from typing import Never, TypeAlias, assert_type

from mote_kernel.execution import Graph

PipelineValue: TypeAlias = str | int


def accept_pipeline_outcome(_value: Graph.Outcome[PipelineValue]) -> None:
    pass


empty_success = Graph.success(Graph.values())
assert_type(empty_success, Graph.SuccessOutcome[Never])
accept_pipeline_outcome(empty_success)

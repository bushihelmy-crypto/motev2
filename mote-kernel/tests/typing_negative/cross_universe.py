from mote_kernel.execution import Graph


async def run_with_wrong_universe(graph: Graph[int]) -> Graph.Result[int]:
    return await graph.run(Graph.values(value="wrong universe"))

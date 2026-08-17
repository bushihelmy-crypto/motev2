from mote_kernel.execution import Graph


class UniverseA:
    pass


class UniverseB:
    pass


def cross_universe_success_result(
    value: Graph.SuccessResult[UniverseA],
) -> Graph.SuccessResult[UniverseB]:
    return value

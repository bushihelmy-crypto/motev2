from mote_kernel.execution import Graph


class UniverseA:
    pass


class UniverseB:
    pass


def cross_universe_transition(value: Graph.Transition[UniverseA]) -> Graph.Transition[UniverseB]:
    return value

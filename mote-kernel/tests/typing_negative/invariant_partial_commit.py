from mote_kernel.execution import Graph


class Animal:
    pass


class Dog(Animal):
    pass


def widen_error(value: Graph.PartialCommitError[Dog]) -> Graph.PartialCommitError[Animal]:
    return value

from mote_kernel.execution import Graph


class Animal:
    pass


class Dog(Animal):
    pass


def widen_continuation(value: Graph.Continuation[Dog]) -> Graph.Continuation[Animal]:
    return value

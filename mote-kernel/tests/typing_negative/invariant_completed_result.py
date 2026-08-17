from mote_kernel.execution import Graph


class Animal:
    pass


class Dog(Animal):
    pass


def widen_completed_result(value: Graph.CompletedResult[Dog]) -> Graph.CompletedResult[Animal]:
    return value

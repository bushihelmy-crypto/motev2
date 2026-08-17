from mote_kernel.execution import Graph


class Animal:
    pass


class Dog(Animal):
    pass


def widen_result(value: Graph.Result[Dog]) -> Graph.Result[Animal]:
    return value

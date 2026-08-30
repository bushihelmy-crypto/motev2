"""Per-function control-flow and effect metrics."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeAlias, cast

from tests.architecture.semantic_model import SymbolDefinition, SymbolId, SymbolKind

FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, order=True, slots=True)
class FunctionComplexity:
    symbol: SymbolId
    path: str
    line: int
    semantic_nodes: int
    decision_points: int
    cyclomatic: int
    cognitive: int
    max_nesting: int
    parameters: int
    returns: int
    raises: int
    awaits: int
    exception_handlers: int
    attribute_writes: int
    task_creations: int


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (owner := _qualified_name(node.value)) is not None:
        return f"{owner}.{node.attr}"
    return None


def _protocol_class(definition: SymbolDefinition) -> bool:
    if definition.symbol.kind is not SymbolKind.CLASS:
        return False
    node = cast(ast.ClassDef, definition.node)
    return any(
        (name := _qualified_name(base.value if isinstance(base, ast.Subscript) else base)) is not None
        and name.rsplit(".", maxsplit=1)[-1] == "Protocol"
        for base in node.bases
    )


class _ComplexityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.decision_points = 0
        self.cognitive = 0
        self.max_nesting = 0
        self.returns = 0
        self.raises = 0
        self.awaits = 0
        self.exception_handlers = 0
        self.attribute_writes = 0
        self.task_creations = 0
        self._nesting = 0
        self._root = True

    def _nested(self, nodes: Iterable[ast.AST]) -> None:
        items = tuple(nodes)
        if not items:
            return
        self._nesting += 1
        self.max_nesting = max(self.max_nesting, self._nesting)
        for node in items:
            self.visit(node)
        self._nesting -= 1

    def _decision(self, node: ast.AST, bodies: tuple[tuple[ast.AST, ...], ...]) -> None:
        self.decision_points += 1
        self.cognitive += 1 + self._nesting
        body_nodes = frozenset(id(item) for body in bodies for item in body)
        for child in ast.iter_child_nodes(node):
            if id(child) not in body_nodes:
                self.visit(child)
        for body in bodies:
            self._nested(body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._root:
            self._root = False
            for statement in node.body:
                self.visit(statement)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self._root:
            self._root = False
            for statement in node.body:
                self.visit(statement)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_If(self, node: ast.If) -> None:
        self._decision(node, (tuple(node.body), tuple(node.orelse)))

    def visit_For(self, node: ast.For) -> None:
        self._decision(node, (tuple(node.body), tuple(node.orelse)))

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._decision(node, (tuple(node.body), tuple(node.orelse)))

    def visit_While(self, node: ast.While) -> None:
        self._decision(node, (tuple(node.body), tuple(node.orelse)))

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.decision_points += 1
        self.cognitive += 1 + self._nesting
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.decision_points += 1 + len(node.ifs)
        self.cognitive += 1 + self._nesting + len(node.ifs)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        points = max(1, len(node.values) - 1)
        self.decision_points += points
        self.cognitive += 1
        self.generic_visit(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        for statement in node.body:
            self.visit(statement)
        for handler in node.handlers:
            self.exception_handlers += 1
            self.decision_points += 1
            self.cognitive += 1 + self._nesting
            self._nested(handler.body)
        self._nested(node.orelse)
        self._nested(node.finalbody)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def visit_Match(self, node: ast.Match) -> None:
        points = max(1, len(node.cases) - 1)
        self.decision_points += points
        self.cognitive += 1 + self._nesting
        self.visit(node.subject)
        for case in node.cases:
            self._nested(case.body)

    def visit_Return(self, node: ast.Return) -> None:
        self.returns += 1
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self.raises += 1
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        self.awaits += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Store):
            self.attribute_writes += 1
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _qualified_name(node.func)
        if name is not None and name.rsplit(".", maxsplit=1)[-1] in {"create_task", "ensure_future"}:
            self.task_creations += 1
        self.generic_visit(node)


def _semantic_node_count(node: FunctionNode) -> int:
    class CounterVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.total = 0
            self._root = True

        def generic_visit(self, node: ast.AST) -> None:
            if not isinstance(node, ast.Load | ast.Store | ast.Del):
                self.total += 1
            super().generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if self._root:
                self._root = False
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if self._root:
                self._root = False
                self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            del node

    visitor = CounterVisitor()
    visitor.visit(node)
    return visitor.total


def _function_complexity(definition: SymbolDefinition) -> FunctionComplexity:
    node = cast(FunctionNode, definition.node)
    visitor = _ComplexityVisitor()
    visitor.visit(node)
    parameters = (
        len(node.args.posonlyargs)
        + len(node.args.args)
        + len(node.args.kwonlyargs)
        + int(node.args.vararg is not None)
        + int(node.args.kwarg is not None)
    )
    return FunctionComplexity(
        definition.symbol,
        definition.path,
        definition.line,
        _semantic_node_count(node),
        visitor.decision_points,
        visitor.decision_points + 1,
        visitor.cognitive,
        visitor.max_nesting,
        parameters,
        visitor.returns,
        visitor.raises,
        visitor.awaits,
        visitor.exception_handlers,
        visitor.attribute_writes,
        visitor.task_creations,
    )


def analyze_function_complexity(definitions: Iterable[SymbolDefinition]) -> tuple[FunctionComplexity, ...]:
    items = tuple(definitions)
    protocol_classes = frozenset(definition.symbol for definition in items if _protocol_class(definition))
    return tuple(
        sorted(
            _function_complexity(definition)
            for definition in items
            if not definition.in_tests
            and definition.owner_class not in protocol_classes
            and definition.symbol.kind in {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION}
        )
    )


__all__ = ["FunctionComplexity", "analyze_function_complexity"]

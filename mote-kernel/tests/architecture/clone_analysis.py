"""Identifier-independent clone detectors for Python syntax trees."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from typing import TypeAlias, cast

from tests.architecture.semantic_index import SemanticIndex
from tests.architecture.semantic_model import ParsedModule, SymbolKind

FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef

FUNCTION_CLONE_MIN_NODES = 22
BRANCH_CLONE_MIN_NODES = 28
STATEMENT_CLONE_MIN_NODES = 28
NEAR_CLONE_MIN_TOKENS = 35
NEAR_CLONE_MIN_SIMILARITY = 85

_NORMALIZED_NAME_FIELDS = frozenset({"arg", "asname", "attr", "id", "module", "name"})
_FUNCTION_KINDS = frozenset({SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION})


@dataclass(frozen=True, order=True, slots=True)
class DefinitionSite:
    path: str
    line: int
    qualified_name: str

    def render(self) -> str:
        return f"{self.path}:{self.line}:{self.qualified_name}"


@dataclass(frozen=True, order=True, slots=True)
class ClonePair:
    kind: str
    left: DefinitionSite
    right: DefinitionSite


@dataclass(frozen=True, order=True, slots=True)
class NearClonePair:
    left: DefinitionSite
    right: DefinitionSite
    similarity: int


@dataclass(frozen=True, slots=True)
class _Function:
    site: DefinitionSite
    node: FunctionNode


@dataclass(frozen=True, slots=True)
class _Block:
    site: DefinitionSite
    owner: DefinitionSite
    statements: tuple[ast.stmt, ...]


@dataclass(frozen=True, slots=True)
class _Record:
    site: DefinitionSite
    node: ast.ClassDef


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (owner := _qualified_name(node.value)) is not None:
        return f"{owner}.{node.attr}"
    return None


def _is_dataclass(node: ast.ClassDef) -> bool:
    return any(
        (name := _qualified_name(decorator.func if isinstance(decorator, ast.Call) else decorator)) is not None
        and name.rsplit(".", maxsplit=1)[-1] == "dataclass"
        for decorator in node.decorator_list
    )


def _without_docstring(statements: Iterable[ast.stmt]) -> tuple[ast.stmt, ...]:
    body = tuple(statements)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _semantic_node_count(nodes: Iterable[ast.AST]) -> int:
    module = ast.Module(body=list(cast(Iterable[ast.stmt], nodes)), type_ignores=[])
    return sum(1 for node in ast.walk(module) if not isinstance(node, ast.Load | ast.Store | ast.Del))


def _shape_value(value: object, *, field: str) -> str:
    if isinstance(value, ast.AST):
        return _node_shape(value)
    if isinstance(value, list):
        items = cast(list[object], value)
        if field == "kwd_attrs":
            return "[" + ",".join("_" for _ in items) + "]"
        return "[" + ",".join(_shape_value(item, field="") for item in items) + "]"
    if field in _NORMALIZED_NAME_FIELDS and isinstance(value, str):
        return "_"
    return f"<{type(value).__name__}>" if field == "value" else repr(value)


def _node_shape(node: ast.AST) -> str:
    parts = [type(node).__name__]
    for field, raw_value in ast.iter_fields(node):
        if field not in {"type_comment", "type_ignores"}:
            parts.append(f"{field}={_shape_value(cast(object, raw_value), field=field)}")
    return "(" + ";".join(parts) + ")"


def _statement_shape(statements: Iterable[ast.stmt]) -> str:
    return _node_shape(ast.Module(body=list(statements), type_ignores=[]))


def _module_functions(modules: Iterable[ParsedModule]) -> tuple[_Function, ...]:
    definitions: list[_Function] = []
    for module in modules:
        for node in module.tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                definitions.append(_Function(DefinitionSite(module.path, node.lineno, node.name), node))
            elif isinstance(node, ast.ClassDef):
                definitions.extend(
                    _Function(DefinitionSite(module.path, member.lineno, f"{node.name}.{member.name}"), member)
                    for member in node.body
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                )
    return tuple(definitions)


def _semantic_functions(index: SemanticIndex) -> tuple[_Function, ...]:
    return tuple(
        _Function(
            DefinitionSite(definition.path, definition.line, definition.symbol.qualified_name),
            cast(FunctionNode, definition.node),
        )
        for definition in index.definitions
        if not definition.in_tests and definition.symbol.kind in _FUNCTION_KINDS
    )


def _dataclasses(modules: Iterable[ParsedModule]) -> tuple[_Record, ...]:
    return tuple(
        _Record(DefinitionSite(module.path, node.lineno, node.name), node)
        for module in modules
        for node in module.tree.body
        if isinstance(node, ast.ClassDef) and _is_dataclass(node)
    )


def _ordered_pair(kind: str, left: DefinitionSite, right: DefinitionSite) -> ClonePair:
    first, second = sorted((left, right))
    return ClonePair(kind, first, second)


def _function_clone_pairs(functions: Iterable[_Function]) -> tuple[ClonePair, ...]:
    by_shape: defaultdict[str, list[DefinitionSite]] = defaultdict(list)
    for function in functions:
        body = _without_docstring(function.node.body)
        if _semantic_node_count(body) >= FUNCTION_CLONE_MIN_NODES:
            by_shape[_statement_shape(body)].append(function.site)
    return tuple(
        sorted(
            _ordered_pair("function", left, right)
            for sites in by_shape.values()
            for left, right in combinations(sorted(set(sites)), 2)
        )
    )


def _control_blocks(function: _Function) -> tuple[_Block, ...]:
    blocks: list[_Block] = []
    for node in ast.walk(function.node):
        if not isinstance(
            node,
            ast.If | ast.For | ast.AsyncFor | ast.While | ast.With | ast.AsyncWith | ast.Try | ast.Match,
        ):
            continue
        for field in ("body", "orelse", "finalbody"):
            raw_statements = getattr(node, field, None)
            if not isinstance(raw_statements, list):
                continue
            statements = tuple(
                statement for statement in cast(list[object], raw_statements) if isinstance(statement, ast.stmt)
            )
            if len(statements) >= 2 and _semantic_node_count(statements) >= BRANCH_CLONE_MIN_NODES:
                blocks.append(
                    _Block(
                        DefinitionSite(function.site.path, statements[0].lineno, function.site.qualified_name),
                        function.site,
                        statements,
                    )
                )
    return tuple(blocks)


def _branch_clone_pairs(
    functions: Iterable[_Function],
    function_pairs: Iterable[ClonePair],
) -> tuple[ClonePair, ...]:
    cloned_owners = {frozenset((pair.left, pair.right)) for pair in function_pairs}
    by_shape: defaultdict[str, list[_Block]] = defaultdict(list)
    for function in functions:
        for block in _control_blocks(function):
            by_shape[_statement_shape(block.statements)].append(block)

    pairs: set[ClonePair] = set()
    for blocks in by_shape.values():
        for left, right in combinations(sorted(set(blocks), key=lambda block: block.site), 2):
            if left.site != right.site and frozenset((left.owner, right.owner)) not in cloned_owners:
                pairs.add(_ordered_pair("branch", left.site, right.site))
    return tuple(sorted(pairs))


def normalized_logic_clones(modules: Iterable[ParsedModule]) -> tuple[ClonePair, ...]:
    functions = _module_functions(modules)
    function_pairs = _function_clone_pairs(functions)
    return tuple(sorted((*function_pairs, *_branch_clone_pairs(functions, function_pairs))))


def _record_field(node: ast.stmt) -> tuple[str, ast.expr, bool] | None:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.annotation, node.value is not None
    return None


def _record_shape(record: _Record) -> str | None:
    fields = [field for node in record.node.body if (field := _record_field(node)) is not None]
    if len(fields) < 2:
        return None
    return "|".join(f"{name}:{_node_shape(annotation)}:{has_default}" for name, annotation, has_default in fields)


def matching_record_shapes(modules: Iterable[ParsedModule]) -> tuple[ClonePair, ...]:
    by_shape: defaultdict[str, list[DefinitionSite]] = defaultdict(list)
    for record in _dataclasses(modules):
        if (shape := _record_shape(record)) is not None:
            by_shape[shape].append(record.site)
    return tuple(
        sorted(
            _ordered_pair("record", left, right)
            for sites in by_shape.values()
            for left, right in combinations(sorted(set(sites)), 2)
        )
    )


class _StatementCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.statements: list[ast.stmt] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.stmt):
            self.statements.append(node)
        super().generic_visit(node)


def _function_statements(function: _Function) -> tuple[ast.stmt, ...]:
    collector = _StatementCollector()
    for statement in _without_docstring(function.node.body):
        collector.visit(statement)
    return tuple(collector.statements)


def normalized_statement_clones(index: SemanticIndex) -> tuple[ClonePair, ...]:
    by_shape: defaultdict[str, list[DefinitionSite]] = defaultdict(list)
    for function in _semantic_functions(index):
        for statement in _function_statements(function):
            if _semantic_node_count((statement,)) >= STATEMENT_CLONE_MIN_NODES:
                site = DefinitionSite(function.site.path, statement.lineno, function.site.qualified_name)
                by_shape[_node_shape(statement)].append(site)
    return tuple(
        sorted(
            _ordered_pair("statement", left, right)
            for sites in by_shape.values()
            for left, right in combinations(sorted(set(sites)), 2)
        )
    )


class _StructureTokens(ast.NodeVisitor):
    def __init__(self) -> None:
        self.values: list[str] = []

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, ast.Load | ast.Store | ast.Del):
            self.values.append(type(node).__name__)
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node


def _structure_tokens(function: _Function) -> tuple[str, ...]:
    visitor = _StructureTokens()
    for statement in _without_docstring(function.node.body):
        visitor.visit(statement)
    return tuple(visitor.values)


def _shingles(tokens: tuple[str, ...], *, width: int = 5) -> Counter[tuple[str, ...]]:
    return Counter(tuple(tokens[position : position + width]) for position in range(len(tokens) - width + 1))


def _similarity(left: Counter[tuple[str, ...]], right: Counter[tuple[str, ...]]) -> int:
    common = sum((left & right).values())
    return 200 * common // (sum(left.values()) + sum(right.values()))


def near_function_clones(index: SemanticIndex) -> tuple[NearClonePair, ...]:
    candidates: list[tuple[_Function, int, Counter[tuple[str, ...]]]] = []
    for function in _semantic_functions(index):
        tokens = _structure_tokens(function)
        if len(tokens) >= NEAR_CLONE_MIN_TOKENS:
            candidates.append((function, len(tokens), _shingles(tokens)))

    pairs: list[NearClonePair] = []
    for (left, left_size, left_shingles), (right, right_size, right_shingles) in combinations(candidates, 2):
        if min(left_size, right_size) * 4 < max(left_size, right_size) * 3:
            continue
        similarity = _similarity(left_shingles, right_shingles)
        if similarity >= NEAR_CLONE_MIN_SIMILARITY:
            first, second = sorted((left.site, right.site))
            pairs.append(NearClonePair(first, second, similarity))
    return tuple(sorted(pairs))


__all__ = [
    "BRANCH_CLONE_MIN_NODES",
    "FUNCTION_CLONE_MIN_NODES",
    "NEAR_CLONE_MIN_SIMILARITY",
    "NEAR_CLONE_MIN_TOKENS",
    "STATEMENT_CLONE_MIN_NODES",
    "ClonePair",
    "DefinitionSite",
    "NearClonePair",
    "matching_record_shapes",
    "near_function_clones",
    "normalized_logic_clones",
    "normalized_statement_clones",
]

"""Typed facts shared by repository-wide Python analyses."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import IntEnum, auto


class SymbolKind(IntEnum):
    CLASS = auto()
    FUNCTION = auto()
    METHOD = auto()
    NESTED_FUNCTION = auto()
    FIELD = auto()
    TYPE_ALIAS = auto()


class ReferenceKind(IntEnum):
    RUNTIME = auto()
    CALL = auto()
    CONSTRUCTION = auto()
    ANNOTATION = auto()
    DECORATOR = auto()
    BASE = auto()
    READ = auto()
    WRITE = auto()


@dataclass(frozen=True, order=True, slots=True)
class SymbolId:
    module: str
    qualified_name: str
    kind: SymbolKind

    @property
    def name(self) -> str:
        return self.qualified_name.rsplit(".", maxsplit=1)[-1]

    def render(self) -> str:
        return f"{self.module}:{self.qualified_name}"


@dataclass(frozen=True, slots=True)
class SymbolDefinition:
    symbol: SymbolId
    path: str
    line: int
    node: ast.AST
    parent: SymbolId | None
    owner_class: SymbolId | None
    private: bool
    implicit: bool
    in_tests: bool


@dataclass(frozen=True, slots=True)
class Reference:
    target: SymbolId
    source: SymbolId | None
    path: str
    line: int
    column: int
    kind: ReferenceKind
    in_tests: bool


@dataclass(frozen=True, slots=True)
class CallSite:
    source: SymbolId | None
    target: SymbolId | None
    path: str
    line: int
    column: int
    awaited: bool
    expression: str
    in_tests: bool


@dataclass(frozen=True, slots=True)
class ParsedModule:
    path: str
    name: str
    tree: ast.Module
    in_tests: bool


__all__ = [
    "CallSite",
    "ParsedModule",
    "Reference",
    "ReferenceKind",
    "SymbolDefinition",
    "SymbolId",
    "SymbolKind",
]

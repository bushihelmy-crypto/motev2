"""Repository-wide semantic facts for general Python quality gates.

The index is deliberately conservative: it records only statically proven
internal references.  Unresolved Python dispatch remains visible as a call
site instead of being guessed from an attribute spelling.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast

from tests.architecture.function_analysis import FunctionComplexity, analyze_function_complexity
from tests.architecture.semantic_model import (
    CallSite,
    ParsedModule,
    Reference,
    ReferenceKind,
    SymbolDefinition,
    SymbolId,
    SymbolKind,
)

FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class SemanticIndex:
    production_modules: tuple[ParsedModule, ...]
    test_modules: tuple[ParsedModule, ...]
    definitions: tuple[SymbolDefinition, ...]
    references: tuple[Reference, ...]
    production_references: Mapping[SymbolId, tuple[Reference, ...]]
    test_references: Mapping[SymbolId, tuple[Reference, ...]]
    calls: tuple[CallSite, ...]
    complexities: tuple[FunctionComplexity, ...]
    production_name_loads: Mapping[str, int]
    production_attribute_loads: Mapping[str, int]
    test_name_loads: Mapping[str, int]
    test_attribute_loads: Mapping[str, int]

    def definition(self, symbol: SymbolId) -> SymbolDefinition:
        return next(definition for definition in self.definitions if definition.symbol == symbol)

    def references_to(self, symbol: SymbolId, *, tests: bool | None = None) -> tuple[Reference, ...]:
        if tests is False:
            return self.production_references.get(symbol, ())
        if tests is True:
            return self.test_references.get(symbol, ())
        return (*self.production_references.get(symbol, ()), *self.test_references.get(symbol, ()))

    def runtime_references_to(self, symbol: SymbolId, *, tests: bool = False) -> tuple[Reference, ...]:
        return tuple(
            reference
            for reference in self.references_to(symbol, tests=tests)
            if reference.kind not in {ReferenceKind.ANNOTATION, ReferenceKind.DECORATOR, ReferenceKind.BASE}
        )

    def call_edges(self, *, tests: bool = False) -> frozenset[tuple[SymbolId, SymbolId]]:
        return frozenset(
            (call.source, call.target)
            for call in self.calls
            if call.in_tests is tests and call.source is not None and call.target is not None
        )


@dataclass(frozen=True, slots=True)
class _ImportBinding:
    module: str
    name: str | None


@dataclass(frozen=True, slots=True)
class _ModuleFacts:
    module: ParsedModule
    definitions: Mapping[str, SymbolId]
    imports: Mapping[str, _ImportBinding]


@dataclass(frozen=True, slots=True)
class _CollectedDefinitions:
    definitions: tuple[SymbolDefinition, ...]
    children: Mapping[SymbolId | None, Mapping[str, SymbolId]]
    fields_by_class: Mapping[SymbolId, Mapping[str, SymbolId]]
    field_types: Mapping[SymbolId, ast.expr]


_IMPLICIT_DECORATORS = frozenset(
    {
        "abstractmethod",
        "classmethod",
        "overload",
        "property",
        "setter",
        "staticmethod",
    }
)


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((root.name, *parts))


def parse_modules(root: Path, *, in_tests: bool) -> tuple[ParsedModule, ...]:
    return tuple(
        ParsedModule(
            path.relative_to(root).as_posix(),
            _module_name(root, path),
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
            in_tests,
        )
        for path in sorted(root.rglob("*.py"))
    )


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified_name(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _decorator_name(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    return _qualified_name(target)


def _class_is_implicit(node: ast.ClassDef) -> bool:
    return any(
        (name := _qualified_name(base.value if isinstance(base, ast.Subscript) else base)) is not None
        and name.rsplit(".", maxsplit=1)[-1] in {"Enum", "IntEnum", "Protocol", "StrEnum"}
        for base in node.bases
    )


def _function_is_implicit(node: FunctionNode, *, implicit_class: bool) -> bool:
    if implicit_class or (node.name.startswith("__") and node.name.endswith("__")):
        return True
    return any(
        (name := _decorator_name(decorator)) is not None and name.rsplit(".", maxsplit=1)[-1] in _IMPLICIT_DECORATORS
        for decorator in node.decorator_list
    )


def _is_overload(node: FunctionNode) -> bool:
    return any(
        (name := _decorator_name(decorator)) is not None and name.rsplit(".", maxsplit=1)[-1] == "overload"
        for decorator in node.decorator_list
    )


def _is_type_alias(node: ast.stmt) -> bool:
    if isinstance(node, ast.AnnAssign):
        name = _qualified_name(node.annotation)
        return name is not None and name.rsplit(".", maxsplit=1)[-1] == "TypeAlias"
    if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
        return False
    name = _qualified_name(node.value.func)
    return name is not None and name.rsplit(".", maxsplit=1)[-1] == "NewType"


def _assignment_names(node: ast.stmt) -> tuple[ast.Name, ...]:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (node.target,)
    if isinstance(node, ast.Assign):
        return tuple(target for target in node.targets if isinstance(target, ast.Name))
    return ()


def _slot_names(node: ast.stmt) -> tuple[tuple[str, int], ...]:
    if not isinstance(node, ast.Assign) or not any(
        isinstance(target, ast.Name) and target.id == "__slots__" for target in node.targets
    ):
        return ()
    values = node.value.elts if isinstance(node.value, ast.Tuple | ast.List | ast.Set) else (node.value,)
    return tuple(
        (value.value, value.lineno)
        for value in values
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    )


def _instance_field_targets(
    node: ast.stmt,
    receiver_names: frozenset[str],
) -> tuple[tuple[ast.Attribute, ast.expr | None, ast.expr | None], ...]:
    if isinstance(node, ast.AnnAssign):
        targets = (node.target,)
        annotation: ast.expr | None = node.annotation
        value = node.value
    elif isinstance(node, ast.Assign):
        targets = tuple(node.targets)
        annotation = None
        value = node.value
    else:
        return ()
    return tuple(
        (target, annotation, value)
        for target in targets
        if isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id in receiver_names
    )


def _collect_definitions(modules: Iterable[ParsedModule]) -> _CollectedDefinitions:
    definitions: list[SymbolDefinition] = []
    children: defaultdict[SymbolId | None, dict[str, SymbolId]] = defaultdict(dict)
    fields_by_class: defaultdict[SymbolId, dict[str, SymbolId]] = defaultdict(dict)
    field_types: dict[SymbolId, ast.expr] = {}

    def parent_private(parent: SymbolId | None) -> bool:
        return parent is not None and next(item.private for item in definitions if item.symbol == parent)

    def receiver_names(parent: SymbolId | None) -> frozenset[str]:
        current = parent
        while current is not None:
            definition = next(item for item in definitions if item.symbol == current)
            if current.kind is SymbolKind.METHOD:
                function = cast(FunctionNode, definition.node)
                positional = (*function.args.posonlyargs, *function.args.args)
                return frozenset((positional[0].arg,)) if positional else frozenset()
            current = definition.parent
        return frozenset()

    def add_field(
        module: ParsedModule,
        owner: SymbolId,
        name: str,
        line: int,
        node: ast.AST,
        *,
        implicit: bool,
        annotation: ast.expr | None = None,
        value: ast.expr | None = None,
    ) -> None:
        existing = fields_by_class[owner].get(name)
        if existing is None:
            symbol = SymbolId(module.name, f"{owner.qualified_name}.{name}", SymbolKind.FIELD)
            definitions.append(
                SymbolDefinition(
                    symbol,
                    module.path,
                    line,
                    node,
                    owner,
                    owner,
                    name.startswith("_") or parent_private(owner),
                    implicit,
                    module.in_tests,
                )
            )
            children[owner][name] = symbol
            fields_by_class[owner][name] = symbol
        else:
            symbol = existing
        if annotation is not None:
            field_types[symbol] = annotation
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name | ast.Attribute):
            field_types[symbol] = value.func

    def collect_statements(
        module: ParsedModule,
        statements: Iterable[ast.stmt],
        *,
        parent: SymbolId | None,
        owner_class: SymbolId | None,
        prefix: str,
        class_implicit: bool,
    ) -> None:
        for statement in statements:
            if isinstance(statement, ast.ClassDef):
                qualified = f"{prefix}.{statement.name}" if prefix else statement.name
                symbol = SymbolId(module.name, qualified, SymbolKind.CLASS)
                private = statement.name.startswith("_") or parent_private(parent)
                implicit = _class_is_implicit(statement)
                definition = SymbolDefinition(
                    symbol,
                    module.path,
                    statement.lineno,
                    statement,
                    parent,
                    owner_class,
                    private,
                    implicit,
                    module.in_tests,
                )
                definitions.append(definition)
                children[parent][statement.name] = symbol
                collect_statements(
                    module,
                    statement.body,
                    parent=symbol,
                    owner_class=symbol,
                    prefix=qualified,
                    class_implicit=implicit,
                )
                continue

            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                if _is_overload(statement):
                    continue
                qualified = f"{prefix}.{statement.name}" if prefix else statement.name
                if parent is None:
                    kind = SymbolKind.FUNCTION
                elif parent.kind is SymbolKind.CLASS:
                    kind = SymbolKind.METHOD
                else:
                    kind = SymbolKind.NESTED_FUNCTION
                symbol = SymbolId(module.name, qualified, kind)
                private = parent_private(parent) or kind is SymbolKind.NESTED_FUNCTION or statement.name.startswith("_")
                implicit = _function_is_implicit(statement, implicit_class=class_implicit)
                definition = SymbolDefinition(
                    symbol,
                    module.path,
                    statement.lineno,
                    statement,
                    parent,
                    owner_class,
                    private,
                    implicit,
                    module.in_tests,
                )
                definitions.append(definition)
                children[parent][statement.name] = symbol
                collect_statements(
                    module,
                    statement.body,
                    parent=symbol,
                    owner_class=owner_class,
                    prefix=qualified,
                    class_implicit=False,
                )
                continue

            if parent is not None and parent.kind is SymbolKind.CLASS:
                slots = _slot_names(statement)
                if slots:
                    for name, line in slots:
                        add_field(
                            module,
                            parent,
                            name,
                            line,
                            statement,
                            implicit=class_implicit,
                        )
                    continue
                for target in _assignment_names(statement):
                    add_field(
                        module,
                        parent,
                        target.id,
                        target.lineno,
                        statement,
                        implicit=class_implicit,
                        annotation=statement.annotation if isinstance(statement, ast.AnnAssign) else None,
                        value=statement.value if isinstance(statement, ast.AnnAssign | ast.Assign) else None,
                    )
                continue

            if parent is None and _is_type_alias(statement):
                for target in _assignment_names(statement):
                    symbol = SymbolId(module.name, target.id, SymbolKind.TYPE_ALIAS)
                    definitions.append(
                        SymbolDefinition(
                            symbol,
                            module.path,
                            target.lineno,
                            statement,
                            None,
                            None,
                            target.id.startswith("_"),
                            False,
                            module.in_tests,
                        )
                    )
                    children[None][target.id] = symbol

            if owner_class is not None:
                for target, annotation, value in _instance_field_targets(statement, receiver_names(parent)):
                    add_field(
                        module,
                        owner_class,
                        target.attr,
                        target.lineno,
                        statement,
                        implicit=class_implicit,
                        annotation=annotation,
                        value=value,
                    )

            for child in ast.iter_child_nodes(statement):
                if isinstance(child, ast.stmt):
                    collect_statements(
                        module,
                        (child,),
                        parent=parent,
                        owner_class=owner_class,
                        prefix=prefix,
                        class_implicit=class_implicit,
                    )
                elif isinstance(child, ast.ExceptHandler | ast.match_case):
                    collect_statements(
                        module,
                        child.body,
                        parent=parent,
                        owner_class=owner_class,
                        prefix=prefix,
                        class_implicit=class_implicit,
                    )

    for module in modules:
        collect_statements(
            module,
            module.tree.body,
            parent=None,
            owner_class=None,
            prefix="",
            class_implicit=False,
        )
    return _CollectedDefinitions(
        tuple(definitions),
        {parent: dict(values) for parent, values in children.items()},
        {owner: dict(values) for owner, values in fields_by_class.items()},
        field_types,
    )


def _absolute_import(module: ParsedModule, imported: str | None, level: int) -> str:
    if level == 0:
        return imported or ""
    package = (
        module.name
        if module.path.endswith("/__init__.py") or module.path == "__init__.py"
        else module.name.rpartition(".")[0]
    )
    parts = package.split(".") if package else []
    retained = parts[: max(0, len(parts) - level + 1)]
    return ".".join((*retained, *((imported or "").split(".") if imported else ())))


def _module_facts(
    modules: Iterable[ParsedModule],
    collected: _CollectedDefinitions,
) -> Mapping[str, _ModuleFacts]:
    top_level: defaultdict[str, dict[str, SymbolId]] = defaultdict(dict)
    for definition in collected.definitions:
        if definition.parent is None:
            top_level[definition.symbol.module][definition.symbol.name] = definition.symbol

    facts: dict[str, _ModuleFacts] = {}
    for module in modules:
        imports: dict[str, _ImportBinding] = {}
        for statement in module.tree.body:
            if isinstance(statement, ast.ImportFrom):
                imported_module = _absolute_import(module, statement.module, statement.level)
                for alias in statement.names:
                    if alias.name != "*":
                        imports[alias.asname or alias.name] = _ImportBinding(imported_module, alias.name)
            elif isinstance(statement, ast.Import):
                for alias in statement.names:
                    local = alias.asname or alias.name.partition(".")[0]
                    imports[local] = _ImportBinding(alias.name, None)
        facts[module.name] = _ModuleFacts(module, top_level[module.name], imports)
    return facts


class _Resolver:
    def __init__(
        self,
        facts: Mapping[str, _ModuleFacts],
        collected: _CollectedDefinitions,
    ) -> None:
        self._facts = facts
        self._definitions = {definition.symbol: definition for definition in collected.definitions}
        self._children = collected.children
        self._fields_by_class = collected.fields_by_class
        self._field_types = collected.field_types

    def exported(self, module: str, name: str, seen: frozenset[tuple[str, str]] = frozenset()) -> SymbolId | None:
        key = (module, name)
        if key in seen:
            return None
        facts = self._facts.get(module)
        if facts is None:
            return None
        direct = facts.definitions.get(name)
        if direct is not None:
            return direct
        imported = facts.imports.get(name)
        if imported is None or imported.name is None:
            return None
        return self.exported(imported.module, imported.name, seen | {key})

    def module_binding(self, module: str, name: str) -> _ImportBinding | None:
        facts = self._facts.get(module)
        return None if facts is None else facts.imports.get(name)

    def child(self, owner: SymbolId, name: str) -> SymbolId | None:
        return self._children.get(owner, {}).get(name)

    def definition(self, symbol: SymbolId) -> SymbolDefinition:
        return self._definitions[symbol]

    def field_type(self, symbol: SymbolId) -> ast.expr | None:
        return self._field_types.get(symbol)


def _bound_names(function: FunctionNode) -> frozenset[str]:
    names = {
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    if function.args.vararg is not None:
        names.add(function.args.vararg.arg)
    if function.args.kwarg is not None:
        names.add(function.args.kwarg.arg)

    class BindingVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            names.add(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            names.add(node.name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            names.add(node.name)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Store):
                names.add(node.id)

    visitor = BindingVisitor()
    for statement in function.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(statement.name)
        else:
            visitor.visit(statement)
    return frozenset(names)


def _annotation_names(annotation: ast.expr | None) -> tuple[str, ...]:
    if annotation is None:
        return ()
    return tuple(node.id for node in ast.walk(annotation) if isinstance(node, ast.Name) and node.id not in {"None"})


class _ReferenceCollector(ast.NodeVisitor):
    def __init__(
        self,
        module: ParsedModule,
        resolver: _Resolver,
        definitions: Mapping[tuple[int, SymbolKind], SymbolDefinition],
    ) -> None:
        self._module = module
        self._resolver = resolver
        self._definitions = definitions
        self._owners: list[SymbolDefinition] = []
        self._classes: list[SymbolId] = []
        self._locals: list[frozenset[str]] = []
        self._local_types: list[dict[str, SymbolId]] = []
        self._annotation = False
        self._decorator = False
        self._base = False
        self._awaited_calls: set[int] = set()
        self.references: set[Reference] = set()
        self.calls: set[CallSite] = set()

    @property
    def _owner(self) -> SymbolId | None:
        return self._owners[-1].symbol if self._owners else None

    def _receiver_names(self) -> frozenset[str]:
        for definition in reversed(self._owners):
            if definition.symbol.kind is not SymbolKind.METHOD:
                continue
            function = cast(FunctionNode, definition.node)
            if any(
                (name := _decorator_name(decorator)) is not None and name.rsplit(".", maxsplit=1)[-1] == "staticmethod"
                for decorator in function.decorator_list
            ):
                return frozenset()
            positional = (*function.args.posonlyargs, *function.args.args)
            return frozenset((positional[0].arg,)) if positional else frozenset()
        return frozenset()

    def _definition_for(self, node: ast.ClassDef | FunctionNode, kinds: tuple[SymbolKind, ...]) -> SymbolDefinition:
        match = next(
            (self._definitions[(node.lineno, kind)] for kind in kinds if (node.lineno, kind) in self._definitions),
            None,
        )
        if match is None:
            raise ValueError(f"missing semantic definition for {self._module.path}:{node.lineno}:{node.name}")
        return match

    def _resolve_name(self, name: str) -> SymbolId | None:
        for index in range(len(self._owners) - 1, -1, -1):
            owner = self._owners[index]
            child = self._resolver.child(owner.symbol, name)
            if child is not None:
                return child
            if (
                owner.symbol.kind in {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION}
                and name in self._locals[index]
            ):
                return None
            if owner.symbol.kind is SymbolKind.METHOD:
                break
        return self._resolver.exported(self._module.name, name)

    def _resolve_annotation(self, annotation: ast.expr | None) -> SymbolId | None:
        candidates = tuple(
            symbol
            for name in _annotation_names(annotation)
            if (symbol := self._resolve_name(name)) is not None and symbol.kind is SymbolKind.CLASS
        )
        return candidates[0] if len(set(candidates)) == 1 else None

    def _resolve_expression_type(self, expression: ast.expr) -> SymbolId | None:
        if isinstance(expression, ast.Name):
            for local_types in reversed(self._local_types):
                if expression.id in local_types:
                    return local_types[expression.id]
            resolved = self._resolve_name(expression.id)
            return resolved if resolved is not None and resolved.kind is SymbolKind.CLASS else None
        if isinstance(expression, ast.Call):
            resolved = self._resolve_callable(expression.func)
            return resolved if resolved is not None and resolved.kind is SymbolKind.CLASS else None
        if isinstance(expression, ast.Attribute):
            resolved = self._resolve_attribute(expression)
            if resolved is None:
                return None
            if resolved.kind is SymbolKind.CLASS:
                return resolved
            annotation = self._resolver.field_type(resolved)
            return self._resolve_annotation(annotation)
        return None

    def _resolve_attribute(self, node: ast.Attribute) -> SymbolId | None:
        if isinstance(node.value, ast.Name):
            if self._classes and node.value.id in self._receiver_names():
                return self._resolver.child(self._classes[-1], node.attr)
            binding = self._resolver.module_binding(self._module.name, node.value.id)
            if binding is not None and binding.name is None:
                return self._resolver.exported(binding.module, node.attr)
        owner_type = self._resolve_expression_type(node.value)
        return None if owner_type is None else self._resolver.child(owner_type, node.attr)

    def _resolve_callable(self, node: ast.expr) -> SymbolId | None:
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
        if isinstance(node, ast.Attribute):
            return self._resolve_attribute(node)
        return None

    def _record(self, node: ast.expr, target: SymbolId, kind: ReferenceKind) -> None:
        self.references.add(
            Reference(
                target,
                self._owner,
                self._module.path,
                node.lineno,
                node.col_offset,
                kind,
                self._module.in_tests,
            )
        )

    def _visit_annotation(self, annotation: ast.expr | None) -> None:
        if annotation is None:
            return
        previous = self._annotation
        self._annotation = True
        self.visit(annotation)
        self._annotation = previous

    def _seed_local_types(self, function: FunctionNode) -> dict[str, SymbolId]:
        types: dict[str, SymbolId] = {}
        for argument in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs):
            resolved = self._resolve_annotation(argument.annotation)
            if resolved is not None:
                types[argument.arg] = resolved
        return types

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            previous = self._decorator
            self._decorator = True
            self.visit(decorator)
            self._decorator = previous
        for base in node.bases:
            previous = self._base
            self._base = True
            self.visit(base)
            self._base = previous
        for keyword in node.keywords:
            self.visit(keyword.value)
        definition = self._definition_for(node, (SymbolKind.CLASS,))
        self._owners.append(definition)
        self._classes.append(definition.symbol)
        self._locals.append(frozenset())
        self._local_types.append({})
        for statement in node.body:
            self.visit(statement)
        self._local_types.pop()
        self._locals.pop()
        self._classes.pop()
        self._owners.pop()

    def _visit_function(self, node: FunctionNode) -> None:
        for decorator in node.decorator_list:
            previous = self._decorator
            self._decorator = True
            self.visit(decorator)
            self._decorator = previous
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self._visit_annotation(argument.annotation)
        if node.args.vararg is not None:
            self._visit_annotation(node.args.vararg.annotation)
        if node.args.kwarg is not None:
            self._visit_annotation(node.args.kwarg.annotation)
        self._visit_annotation(node.returns)
        if _is_overload(node):
            return
        definition = self._definition_for(
            node,
            (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION),
        )
        self._owners.append(definition)
        self._locals.append(_bound_names(node))
        self._local_types.append(self._seed_local_types(node))
        for statement in node.body:
            self.visit(statement)
        self._local_types.pop()
        self._locals.pop()
        self._owners.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Await(self, node: ast.Await) -> None:
        if isinstance(node.value, ast.Call):
            self._awaited_calls.add(id(node.value))
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._resolve_callable(node.func)
        expression = _qualified_name(node.func) or type(node.func).__name__
        self.calls.add(
            CallSite(
                self._owner,
                target,
                self._module.path,
                node.lineno,
                node.col_offset,
                id(node) in self._awaited_calls,
                expression,
                self._module.in_tests,
            )
        )
        if target is not None:
            kind = ReferenceKind.CONSTRUCTION if target.kind is SymbolKind.CLASS else ReferenceKind.CALL
            self._record(node.func, target, kind)
            if target.kind is SymbolKind.CLASS:
                for keyword in node.keywords:
                    if keyword.arg is not None and (field := self._resolver.child(target, keyword.arg)) is not None:
                        self._record(keyword.value, field, ReferenceKind.RUNTIME)
        else:
            self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        target = self._resolve_name(node.id)
        if target is None:
            return
        if self._annotation:
            kind = ReferenceKind.ANNOTATION
        elif self._decorator:
            kind = ReferenceKind.DECORATOR
        elif self._base:
            kind = ReferenceKind.BASE
        else:
            kind = ReferenceKind.RUNTIME
        self._record(node, target, kind)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        target = self._resolve_attribute(node)
        if target is not None:
            if self._annotation:
                kind = ReferenceKind.ANNOTATION
            elif isinstance(node.ctx, ast.Store):
                kind = ReferenceKind.WRITE
            elif target.kind is SymbolKind.FIELD:
                kind = ReferenceKind.READ
            else:
                kind = ReferenceKind.RUNTIME
            self._record(node, target, kind)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._visit_annotation(node.annotation)
        resolved = self._resolve_annotation(node.annotation)
        if resolved is not None and self._local_types and isinstance(node.target, ast.Name):
            self._local_types[-1][node.target.id] = resolved
        if node.value is not None:
            inferred = self._resolve_expression_type(node.value)
            if inferred is not None and self._local_types and isinstance(node.target, ast.Name):
                self._local_types[-1][node.target.id] = inferred
            self.visit(node.value)
        self.visit(node.target)

    def visit_Assign(self, node: ast.Assign) -> None:
        inferred = self._resolve_expression_type(node.value)
        if inferred is not None and self._local_types:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._local_types[-1][target.id] = inferred
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)


def build_semantic_index(package_root: Path, test_root: Path) -> SemanticIndex:
    production = parse_modules(package_root, in_tests=False)
    tests = parse_modules(test_root, in_tests=True)
    modules = (*production, *tests)
    collected = _collect_definitions(modules)
    facts = _module_facts(modules, collected)
    resolver = _Resolver(facts, collected)
    definitions_by_module: defaultdict[str, dict[tuple[int, SymbolKind], SymbolDefinition]] = defaultdict(dict)
    for definition in collected.definitions:
        definitions_by_module[definition.symbol.module][(definition.line, definition.symbol.kind)] = definition

    references: set[Reference] = set()
    calls: set[CallSite] = set()
    for module in modules:
        collector = _ReferenceCollector(module, resolver, definitions_by_module[module.name])
        collector.visit(module.tree)
        references.update(collector.references)
        calls.update(collector.calls)

    complexities = analyze_function_complexity(collected.definitions)

    def loads(selected: Iterable[ParsedModule]) -> tuple[Mapping[str, int], Mapping[str, int]]:
        names: Counter[str] = Counter()
        attributes: Counter[str] = Counter()
        for module in selected:
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    names[node.id] += 1
                elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    attributes[node.attr] += 1
        return dict(names), dict(attributes)

    production_names, production_attributes = loads(production)
    test_names, test_attributes = loads(tests)
    ordered_references = tuple(
        sorted(
            references,
            key=lambda reference: (
                reference.target,
                reference.path,
                reference.line,
                reference.column,
                reference.kind,
            ),
        )
    )
    grouped_references: tuple[dict[SymbolId, list[Reference]], dict[SymbolId, list[Reference]]] = ({}, {})
    for reference in ordered_references:
        selected = grouped_references[int(reference.in_tests)]
        selected.setdefault(reference.target, []).append(reference)
    return SemanticIndex(
        production,
        tests,
        tuple(sorted(collected.definitions, key=lambda definition: definition.symbol)),
        ordered_references,
        {symbol: tuple(values) for symbol, values in grouped_references[0].items()},
        {symbol: tuple(values) for symbol, values in grouped_references[1].items()},
        tuple(
            sorted(
                calls,
                key=lambda call: (
                    call.path,
                    call.line,
                    call.column,
                    call.expression,
                ),
            )
        ),
        complexities,
        production_names,
        production_attributes,
        test_names,
        test_attributes,
    )


def call_counts(index: SemanticIndex) -> Counter[SymbolId]:
    return Counter(call.target for call in index.calls if not call.in_tests and call.target is not None)


def call_adjacency(index: SemanticIndex) -> Mapping[SymbolId, frozenset[SymbolId]]:
    adjacency: defaultdict[SymbolId, set[SymbolId]] = defaultdict(set)
    for source, target in index.call_edges():
        if target.kind in {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.NESTED_FUNCTION}:
            adjacency[source].add(target)
    return {source: frozenset(targets) for source, targets in adjacency.items()}


def strongly_connected_calls(index: SemanticIndex) -> tuple[frozenset[SymbolId], ...]:
    adjacency = call_adjacency(index)
    nodes = frozenset((*adjacency, *(target for targets in adjacency.values() for target in targets)))
    serial = 0
    indices: dict[SymbolId, int] = {}
    lowlinks: dict[SymbolId, int] = {}
    stack: list[SymbolId] = []
    active: set[SymbolId] = set()
    components: list[frozenset[SymbolId]] = []

    def connect(node: SymbolId) -> None:
        nonlocal serial
        indices[node] = serial
        lowlinks[node] = serial
        serial += 1
        stack.append(node)
        active.add(node)
        for target in adjacency.get(node, frozenset()):
            if target not in indices:
                connect(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in active:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: set[SymbolId] = set()
        while True:
            member = stack.pop()
            active.remove(member)
            component.add(member)
            if member == node:
                break
        if len(component) > 1 or node in adjacency.get(node, frozenset()):
            components.append(frozenset(component))

    for node in sorted(nodes):
        if node not in indices:
            connect(node)
    return tuple(sorted(components, key=lambda component: tuple(sorted(component))))


def unresolved_production_calls(index: SemanticIndex) -> tuple[CallSite, ...]:
    return tuple(call for call in index.calls if not call.in_tests and call.target is None)


__all__ = [
    "SemanticIndex",
    "build_semantic_index",
    "call_adjacency",
    "call_counts",
    "parse_modules",
    "strongly_connected_calls",
    "unresolved_production_calls",
]

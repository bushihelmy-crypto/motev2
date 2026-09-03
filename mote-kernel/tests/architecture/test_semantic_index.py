from pathlib import Path

from tests.architecture.quality_analysis import (
    call_graph_metrics,
    class_cohesion_candidates,
    cyclic_module_dependencies,
    module_dependency_metrics,
    orphaned_task_handles,
    production_unreferenced_definitions,
    runtime_module_call_metrics,
    symbol_usages,
    unconsumed_internal_async_calls,
    unowned_internal_coroutine_handles,
    unread_private_fields,
    unused_private_definitions,
)
from tests.architecture.quality_analysis import (
    test_only_private_definitions as private_definitions_used_only_by_tests,
)
from tests.architecture.semantic_index import SemanticIndex, build_semantic_index, strongly_connected_calls
from tests.architecture.semantic_model import ReferenceKind, SymbolKind


def _build_index(
    tmp_path: Path,
    production: dict[str, str],
    tests: dict[str, str] | None = None,
) -> SemanticIndex:
    package_root = tmp_path / "package"
    test_root = tmp_path / "tests"
    package_root.mkdir()
    test_root.mkdir()
    for relative, source in production.items():
        path = package_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    for relative, source in (tests or {}).items():
        path = test_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return build_semantic_index(package_root, test_root)


def test_qualified_symbols_keep_same_names_and_imported_calls_distinct(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "left.py": "def _step() -> None:\n    return None\n",
            "right.py": "def _step() -> None:\n    return None\n",
            "caller.py": "from package.left import _step\n\ndef run() -> None:\n    _step()\n",
        },
    )

    steps = tuple(definition.symbol for definition in index.definitions if definition.symbol.name == "_step")
    call = next(call for call in index.calls if call.expression == "_step")

    assert len(steps) == 2
    assert call.target is not None
    assert call.target.module == "package.left"
    assert len(index.references_to(call.target, tests=False)) == 1
    assert index.references_to(next(symbol for symbol in steps if symbol.module == "package.right"), tests=False) == ()


def test_methods_fields_and_callbacks_are_resolved_without_receiver_name_conventions(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "model.py": """
class Worker:
    _value: int

    def __init__(this) -> None:
        this._value = 1

    def _read(this) -> int:
        return this._value

    def run(this) -> int:
        return this._read()

def _callback(value: int) -> int:
    return value

def dispatch(consumer) -> None:
    consumer(_callback)
""",
        },
    )

    read = next(definition.symbol for definition in index.definitions if definition.symbol.name == "_read")
    field = next(definition.symbol for definition in index.definitions if definition.symbol.name == "_value")
    callback = next(definition.symbol for definition in index.definitions if definition.symbol.name == "_callback")

    assert any(call.target == read for call in index.calls)
    assert {reference.kind for reference in index.references_to(field, tests=False)} == {
        ReferenceKind.READ,
        ReferenceKind.WRITE,
    }
    callback_usage = next(usage for usage in symbol_usages(index) if usage.definition.symbol == callback)
    assert {reference.kind for reference in callback_usage.production_runtime_references} == {ReferenceKind.RUNTIME}


def test_overload_declarations_do_not_duplicate_the_runtime_symbol(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "parse.py": """
from typing import overload

@overload
def parse(value: str) -> str: ...

@overload
def parse(value: bytes) -> bytes: ...

def parse(value: str | bytes) -> str | bytes:
    return value
""",
        },
    )

    definitions = tuple(definition for definition in index.definitions if definition.symbol.name == "parse")

    assert len(definitions) == 1
    assert definitions[0].symbol.kind is SymbolKind.FUNCTION


def test_async_ownership_detectors_separate_awaited_calls_and_owned_tasks(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "async_flow.py": """
import asyncio

async def _fetch() -> str:
    return "value"

async def good() -> str:
    return await _fetch()

async def bad() -> None:
    _fetch()

async def leaked() -> None:
    pending = _fetch()

async def tasks() -> None:
    orphan = asyncio.create_task(_fetch())
    owned = asyncio.create_task(_fetch())
    await owned
""",
        },
    )

    discarded = unconsumed_internal_async_calls(index)
    unowned = unowned_internal_coroutine_handles(index)
    orphaned = orphaned_task_handles(index)

    assert tuple(violation.expression for violation in discarded) == ("_fetch",)
    assert tuple(violation.variable for violation in unowned) == ("pending",)
    assert tuple(violation.variable for violation in orphaned) == ("orphan",)


def test_usage_detectors_distinguish_dead_test_only_and_unread_symbols(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "usage.py": """
class Cache:
    def __init__(self) -> None:
        self._write_only = 1

def _unused() -> None:
    return None

def _test_view() -> str:
    return "value"
""",
        },
        {
            "test_usage.py": """
from package.usage import _test_view

def test_view() -> None:
    assert _test_view()
"""
        },
    )

    assert tuple(site.symbol.name for site in unused_private_definitions(index)) == ("_unused",)
    assert tuple(site.symbol.name for site in private_definitions_used_only_by_tests(index)) == ("_test_view",)
    assert tuple(site.symbol.name for site in unread_private_fields(index)) == ("_write_only",)
    assert {site.symbol.name for site in production_unreferenced_definitions(index)} == {
        "Cache",
        "_test_view",
        "_unused",
    }


def test_function_metrics_cover_control_flow_nesting_and_effects(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "effects.py": """
import asyncio

class Worker:
    async def run(self, values: tuple[int, ...]) -> None:
        self._active = True
        for value in values:
            if value:
                try:
                    await asyncio.sleep(value)
                except ValueError:
                    raise
        asyncio.create_task(asyncio.sleep(0))
""",
        },
    )

    complexity = next(item for item in index.complexities if item.symbol.name == "run")

    assert complexity.decision_points == 3
    assert complexity.cyclomatic == 4
    assert complexity.cognitive == 6
    assert complexity.max_nesting == 3
    assert complexity.parameters == 2
    assert complexity.awaits == 1
    assert complexity.exception_handlers == 1
    assert complexity.attribute_writes == 1
    assert complexity.task_creations == 1


def test_parameterized_protocol_stubs_are_not_runtime_complexity(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "port.py": """
from typing import Protocol, TypeVar

T = TypeVar("T")

class Port(Protocol[T]):
    def read(self) -> T: ...

def execute() -> None:
    return None
""",
        },
    )

    assert tuple(item.symbol.name for item in index.complexities) == ("execute",)


def test_class_cohesion_connects_methods_through_calls_and_shared_fields(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "service.py": """
class Service:
    def __init__(self) -> None:
        self._left = 1
        self._right = 2

    def left(self) -> int:
        return self._left

    def delegated_left(self) -> int:
        return self.left()

    def right(self) -> int:
        return self._right

    def isolated(self) -> int:
        return 3
""",
        },
    )

    cohesion = class_cohesion_candidates(index)

    assert len(cohesion) == 1
    assert cohesion[0].methods == 4
    assert cohesion[0].fields == 2
    assert cohesion[0].components == 3


def test_call_graph_reports_recursion_and_transitive_burden(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "calls.py": """
def first(value: int) -> int:
    if value:
        return second(value - 1)
    return value

def second(value: int) -> int:
    return first(value)

def root(value: int) -> int:
    return first(value)
""",
        },
    )

    components = strongly_connected_calls(index)
    metrics = call_graph_metrics(index)

    assert len(components) == 1
    assert {symbol.name for symbol in components[0]} == {"first", "second"}
    assert metrics.edge_count == 3
    assert metrics.recursive_components == 1
    assert metrics.max_chain_depth == 2
    assert metrics.max_chain_cognitive >= 1


def test_runtime_module_call_metrics_measure_resolved_callable_coupling(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "left.py": ("class Marker:\n    pass\n\ndef leaf() -> None:\n    return None\n"),
            "right.py": (
                "from package.left import Marker, leaf\n\ndef run() -> None:\n    leaf()\n    leaf()\n    Marker()\n"
            ),
            "third.py": ("from package.left import leaf\n\ndef other() -> None:\n    leaf()\n"),
        },
    )

    metrics = runtime_module_call_metrics(index)

    assert metrics.edge_count == 3
    assert metrics.module_pair_count == 2
    assert metrics.max_fan_out == 1
    assert [(pair.source_module, pair.target_module, pair.symbol_edges, pair.call_sites) for pair in metrics.pairs] == [
        ("package.right", "package.left", 2, 3),
        ("package.third", "package.left", 1, 1),
    ]


def test_module_dependency_graph_resolves_relative_imports_and_cycles(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "__init__.py": "",
            "left.py": "from .right import run_right\n\ndef run_left() -> None:\n    run_right()\n",
            "right.py": "from .left import run_left\n\ndef run_right() -> None:\n    run_left()\n",
        },
    )

    cycles = cyclic_module_dependencies(index)
    metrics = module_dependency_metrics(index)

    assert cycles == (frozenset({"package.left", "package.right"}),)
    assert metrics.edge_count == 2
    assert metrics.cyclic_components == 1
    assert metrics.max_fan_out == 1
    assert metrics.max_depth == 1

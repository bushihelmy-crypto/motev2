import tomllib
from pathlib import Path

from tests.architecture.generic_rules import (
    production_type_erasure_violations,
    type_erasure_violations,
)

PROJECT_ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "mote_kernel"


def test_strict_type_gate_cannot_be_downgraded() -> None:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyright = configuration["tool"]["pyright"]

    assert pyright["typeCheckingMode"] == "strict"
    assert pyright["include"] == ["src", "tests"]
    assert pyright["exclude"] == ["tests/typing_negative"]
    assert pyright["reportMissingTypeStubs"] == "error"


def test_production_boundaries_preserve_generic_types() -> None:
    """Reject syntax-level erasure; Pyright fixtures own relational typing proof."""

    violations = production_type_erasure_violations(PACKAGE_ROOT)
    assert not violations, (
        "production annotations must not erase types through bare generics, object boundaries, "
        f"or erased casts: {violations}"
    )


def test_generic_gate_accepts_a_preserved_relationship() -> None:
    source = """
from dataclasses import dataclass
from typing import Generic, TypeVar

OutputT = TypeVar("OutputT")

@dataclass(frozen=True)
class Definition(Generic[OutputT]):
    output: OutputT

def run(definition: Definition[OutputT]) -> OutputT:
    return definition.output
"""

    assert type_erasure_violations(source) == ()


def test_generic_gate_accepts_parameterized_containers() -> None:
    source = """
from collections.abc import Mapping

def index(values: tuple[str, ...]) -> Mapping[str, list[int]]:
    return {value: [position] for position, value in enumerate(values)}
"""

    assert type_erasure_violations(source) == ()


def test_generic_gate_rejects_erased_relationships() -> None:
    source = """
from typing import cast

def bare(values: list) -> dict:
    return {}

def erased(value: object) -> object:
    return cast(object, value)
"""

    messages = {violation.message for violation in type_erasure_violations(source)}
    assert messages == {
        "bare generic annotation dict",
        "bare generic annotation list",
        "cast cannot restore an erased generic type",
        "object erases the boundary type",
    }

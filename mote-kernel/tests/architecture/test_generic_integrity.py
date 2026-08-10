import tomllib
from pathlib import Path

from tests.architecture.generic_rules import generic_violations, production_generic_violations

PROJECT_ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "mote_kernel"


def test_strict_type_gate_cannot_be_downgraded() -> None:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pyright = configuration["tool"]["pyright"]

    assert pyright["typeCheckingMode"] == "strict"
    assert pyright["include"] == ["src", "tests"]
    assert pyright["reportMissingTypeStubs"] == "error"


def test_production_boundaries_preserve_generic_types() -> None:
    violations = production_generic_violations(PACKAGE_ROOT)
    assert not violations, f"generic relationships must remain explicit end to end: {violations}"


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

    assert generic_violations(source) == ()


def test_generic_gate_rejects_erased_relationships() -> None:
    source = """
from typing import cast

def bare(values: list) -> dict:
    return {}

def erased(value: object) -> object:
    return cast(object, value)
"""

    messages = {violation.message for violation in generic_violations(source)}
    assert messages == {
        "bare generic annotation dict",
        "bare generic annotation list",
        "cast cannot restore an erased generic type",
        "object erases the boundary type",
    }

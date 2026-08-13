from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mote_kernel"

REQUIRED_PACKAGES = frozenset(
    {
        "act",
        "act/tool_use",
        "events",
        "execution",
        "execution/engine",
        "execution/graph",
        "extensions",
        "extensions/code_map",
        "failover",
        "hooks",
        "logging",
        "observability",
        "observe",
        "operations",
        "execution/resource",
        "role",
        "role/restore",
        "state",
        "state/graph_state",
        "think",
        "think/commands",
        "think/prompt",
        "think/routing",
        "tools",
        "turn_context",
        "turn_context/compact",
        "workflow",
    }
)

FORBIDDEN_PACKAGE_NAMES = frozenset({"common", "helper", "helpers", "misc", "shared", "util", "utils"})


def _is_production_directory(path: Path) -> bool:
    relative = path.relative_to(PACKAGE_ROOT)
    return path.is_dir() and path.name != "__pycache__" and not any(part.startswith(".") for part in relative.parts)


def test_confirmed_kernel_packages_exist() -> None:
    missing = sorted(path for path in REQUIRED_PACKAGES if not (PACKAGE_ROOT / path / "__init__.py").is_file())
    assert not missing, f"missing confirmed Kernel packages: {missing}"


def test_generic_ownerless_packages_are_forbidden() -> None:
    violations = sorted(
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_dir() and path.name in FORBIDDEN_PACKAGE_NAMES
    )
    assert not violations, f"ownerless generic packages are forbidden: {violations}"


def test_every_production_package_is_explicit() -> None:
    violations = sorted(
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*")
        if _is_production_directory(path) and not (path / "__init__.py").is_file()
    )
    assert not violations, f"production package directories require __init__.py: {violations}"

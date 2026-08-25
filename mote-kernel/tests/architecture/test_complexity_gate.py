from dataclasses import replace
from pathlib import Path

from tests.architecture.complexity_rules import (
    HEALTH_METRIC_NAMES,
    RATCHET_METRIC_NAMES,
    ComplexityCandidateInventory,
    candidate_inventory,
    complexity_snapshot,
    health_target_gaps,
    load_metric_limits,
    load_reviewed_candidates,
    render_complexity_report,
)

PROJECT_ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "mote_kernel"
TEST_ROOT = PROJECT_ROOT / "tests"


def _complexity_limits() -> dict[str, int]:
    return load_metric_limits(
        PROJECT_ROOT,
        table_name="complexity_ratchet",
        expected_names=RATCHET_METRIC_NAMES,
    )


def test_health_targets_do_not_accept_unreviewed_structural_smells() -> None:
    targets = load_metric_limits(
        PROJECT_ROOT,
        table_name="complexity_health",
        expected_names=HEALTH_METRIC_NAMES,
    )

    assert targets == dict.fromkeys(HEALTH_METRIC_NAMES, 0)


def test_current_candidates_are_explicitly_reviewed_and_inventory_is_fresh() -> None:
    snapshot = complexity_snapshot(PACKAGE_ROOT, TEST_ROOT)
    actual = candidate_inventory(snapshot)
    reviewed = load_reviewed_candidates(PROJECT_ROOT)
    targets = load_metric_limits(
        PROJECT_ROOT,
        table_name="complexity_health",
        expected_names=HEALTH_METRIC_NAMES,
    )

    assert actual.difference(reviewed).total() == 0
    assert reviewed.difference(actual).total() == 0
    assert health_target_gaps(snapshot, targets, reviewed) == ()


def test_reviewed_candidates_do_not_mask_new_or_stale_identities(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    test_root = tmp_path / "tests"
    package_root.mkdir()
    test_root.mkdir()
    (package_root / "logic.py").write_text(
        """
def select_large(values: tuple[int, ...]) -> tuple[int, ...]:
    selected: list[int] = []
    for value in values:
        if value > 10:
            selected.append(value * 2)
    return tuple(selected)

def retain_ready(items: tuple[int, ...]) -> tuple[int, ...]:
    ready: list[int] = []
    for item in items:
        if item > 99:
            ready.append(item * 7)
    return tuple(ready)
""",
        encoding="utf-8",
    )

    snapshot = complexity_snapshot(package_root, test_root)
    actual = candidate_inventory(snapshot)
    empty_review = ComplexityCandidateInventory(
        logical_clone_pairs=frozenset(),
        record_shape_clone_pairs=frozenset(),
        thin_single_use_helpers=frozenset(),
        single_use_private_dataclasses=frozenset(),
        test_only_private_definitions=frozenset(),
    )
    targets = dict.fromkeys(HEALTH_METRIC_NAMES, 0)

    assert health_target_gaps(snapshot, targets, empty_review) == (("logical_clone_pairs", 0, 1),)
    assert health_target_gaps(snapshot, targets, actual) == ()
    stale_review = replace(empty_review, logical_clone_pairs=frozenset({"stale candidate"}))
    assert stale_review.difference(actual).logical_clone_pairs == frozenset({"stale candidate"})


def test_structural_complexity_does_not_grow_and_improvements_are_ratchet_locked() -> None:
    snapshot = complexity_snapshot(PACKAGE_ROOT, TEST_ROOT)
    actual = dict(snapshot.metric_items())
    limits = _complexity_limits()
    regressions = {name: (limits[name], value) for name, value in actual.items() if value > limits[name]}
    unratcheted_improvements = {name: (limits[name], value) for name, value in actual.items() if value < limits[name]}

    assert not regressions, (
        "structural complexity grew; remove the new indirection/duplicate shape or explicitly review the baseline "
        f"change (metric: configured -> actual): {regressions}\n\n{render_complexity_report(snapshot)}"
    )
    assert not unratcheted_improvements, (
        "structural complexity improved; lower the checked-in limits so the reduction cannot regress "
        f"(metric: configured -> actual): {unratcheted_improvements}"
    )


def test_normalized_logic_clones_survive_identifier_and_literal_changes(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    test_root = tmp_path / "tests"
    package_root.mkdir()
    test_root.mkdir()
    (package_root / "logic.py").write_text(
        """
def select_large(values: tuple[int, ...]) -> tuple[int, ...]:
    selected: list[int] = []
    for value in values:
        if value > 10:
            selected.append(value * 2)
    return tuple(selected)

def retain_ready(items: tuple[int, ...]) -> tuple[int, ...]:
    ready: list[int] = []
    for item in items:
        if item > 99:
            ready.append(item * 7)
    return tuple(ready)
""",
        encoding="utf-8",
    )

    snapshot = complexity_snapshot(package_root, test_root)

    assert snapshot.logical_clone_pairs == 1
    assert snapshot.logical_clones[0].kind == "function"


def test_normalized_branch_clones_are_visible_when_whole_functions_differ(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    test_root = tmp_path / "tests"
    package_root.mkdir()
    test_root.mkdir()
    (package_root / "branches.py").write_text(
        """
def count_large(enabled: bool, values: tuple[int, ...]) -> int:
    count = 0
    if enabled:
        selected: list[int] = []
        for value in values:
            if value > 10:
                selected.append(value * 2)
        count = len(selected)
    return count

def count_ready(ready: bool, items: tuple[int, ...]) -> int:
    if not ready:
        return -1
    total = 0
    if ready:
        accepted: list[int] = []
        for item in items:
            if item > 99:
                accepted.append(item * 7)
        total = len(accepted)
    return total
""",
        encoding="utf-8",
    )

    snapshot = complexity_snapshot(package_root, test_root)

    assert snapshot.logical_clone_pairs == 1
    assert snapshot.logical_clones[0].kind == "branch"


def test_matching_record_shapes_are_detected_across_nominal_types(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    test_root = tmp_path / "tests"
    package_root.mkdir()
    test_root.mkdir()
    (package_root / "records.py").write_text(
        """
from dataclasses import dataclass

@dataclass(frozen=True)
class PlannedItem:
    name: str
    position: int

@dataclass(frozen=True)
class AdmittedItem:
    name: bytes
    position: float
""",
        encoding="utf-8",
    )

    snapshot = complexity_snapshot(package_root, test_root)

    assert snapshot.record_shape_clone_pairs == 1


def test_private_production_definitions_used_only_by_tests_are_visible(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    test_root = tmp_path / "tests"
    package_root.mkdir()
    test_root.mkdir()
    (package_root / "adapter.py").write_text(
        """
def _test_view(value: str) -> str:
    return value
""",
        encoding="utf-8",
    )
    (test_root / "test_adapter.py").write_text(
        """
from package.adapter import _test_view

def test_view() -> None:
    assert _test_view("value") == "value"
""",
        encoding="utf-8",
    )

    snapshot = complexity_snapshot(package_root, test_root)

    assert snapshot.test_only_private_definitions == 1
    assert snapshot.thin_single_use_helpers == 1


def test_single_use_private_dataclasses_are_visible(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    test_root = tmp_path / "tests"
    package_root.mkdir()
    test_root.mkdir()
    (package_root / "record.py").write_text(
        """
from dataclasses import dataclass

@dataclass(frozen=True)
class _Adapter:
    value: str

ADAPTER = _Adapter("value")
""",
        encoding="utf-8",
    )

    snapshot = complexity_snapshot(package_root, test_root)

    assert snapshot.single_use_private_dataclasses == 1

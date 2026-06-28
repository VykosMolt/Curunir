"""Fast contracts for the tiny_256 coverage source planner."""

from pathlib import Path
import statistics

import pytest

from argus_capsules.codec_coverage_planner import (
    STRATEGIES,
    compute_coverage_metrics,
    heldout_overlap_audit,
    plan_train_examples,
    run_coverage_planner,
    tiny256_coverage_planner_verdict,
    _combo_set,
    _example_from_indices,
    _field_count_vector,
)
from argus_capsules.tiny_schema import TinySchemaSpec


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _values(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"{prefix}_{index:02d}" for index in range(count))


def _dates(count: int) -> tuple[str, ...]:
    return tuple(f"2026-01-{index + 1:02d}" for index in range(count))


def _spec(
    *,
    subjects: int = 4,
    predicates: int = 2,
    objects: int = 4,
    dates: int = 4,
    values: int = 4,
) -> TinySchemaSpec:
    return TinySchemaSpec(
        name="fixture",
        subjects=_values("subject", subjects),
        predicates=_values("predicate", predicates),
        objects=_values("object", objects),
        dates=_dates(dates),
        values=_values("value", values),
        confidences=("low", "high"),
        source_types=("synthetic_note", "synthetic_log"),
    )


def _example(
    spec: TinySchemaSpec,
    subject: int,
    object_: int,
    value: int,
    *,
    ordinal: int = 0,
    date: int = 0,
    predicate: int = 0,
):
    return _example_from_indices(
        spec,
        {
            "subject": subject,
            "predicate": predicate,
            "object": object_,
            "date": date,
            "value": value,
            "confidence": ordinal,
            "source_type": ordinal,
        },
        ordinal=ordinal,
    )


def test_coverage_metrics_basic():
    spec = _spec(subjects=2, predicates=1, objects=2, dates=2, values=2)
    train = [
        _example(spec, 0, 0, 0, ordinal=0, date=0),
        _example(spec, 1, 1, 1, ordinal=1, date=1),
    ]
    heldout = [
        _example(spec, 0, 0, 0, ordinal=2, date=0),
        _example(spec, 0, 1, 0, ordinal=3, date=1),
    ]
    metrics = compute_coverage_metrics(
        spec,
        train,
        heldout,
        train_size=2,
        seed=0,
        strategy="fixture",
    )
    assert metrics["subject_value_pair_coverage"] == pytest.approx(0.5)
    assert metrics["object_value_pair_coverage"] == pytest.approx(0.5)
    assert metrics["heldout_subject_value_pair_seen_rate"] == pytest.approx(1.0)
    assert metrics["heldout_object_value_pair_seen_rate"] == pytest.approx(0.5)
    assert metrics["subject_marginal_entropy"] == pytest.approx(1.0)
    assert metrics["value_class_min_count"] == 1
    assert metrics["value_class_max_count"] == 1


def test_coverage_planner_no_heldout_label_dependency_primary():
    spec = _spec(subjects=3, predicates=2, objects=3, dates=4, values=4)
    for strategy in STRATEGIES:
        examples, metadata = plan_train_examples(
            spec,
            train_size=6,
            seed=0,
            strategy=strategy,
        )
        assert len(examples) == 6
        assert metadata["heldout_labels_required"] is False


def test_exact_heldout_collision_detection():
    spec = _spec(subjects=2, predicates=1, objects=2, dates=2, values=2)
    exact = _example(spec, 0, 0, 0, ordinal=0, date=0)
    same_sov_different_report = _example(spec, 0, 0, 0, ordinal=1, date=1)
    train = [exact]
    heldout = [exact, same_sov_different_report]
    audit = heldout_overlap_audit(train, heldout)
    assert audit["exact_heldout_sov_collision_count"] == 1
    assert audit["exact_heldout_sov_collision_row_count"] == 2
    assert audit["exact_heldout_full_report_collision_count"] == 1
    assert audit["exact_heldout_full_report_collision_row_count"] == 1


def test_value_pair_coverage_greedy_improves_over_random_small():
    spec = _spec(subjects=2, predicates=2, objects=2, dates=2, values=2)
    random_examples, _ = plan_train_examples(
        spec, train_size=4, seed=0, strategy="random_baseline"
    )
    greedy_examples, _ = plan_train_examples(
        spec, train_size=4, seed=0, strategy="value_pair_coverage_greedy"
    )
    random_sv = len(_combo_set(random_examples, ("subject", "value")))
    greedy_sv = len(_combo_set(greedy_examples, ("subject", "value")))
    random_ov = len(_combo_set(random_examples, ("object", "value")))
    greedy_ov = len(_combo_set(greedy_examples, ("object", "value")))
    assert greedy_sv > random_sv or greedy_ov > random_ov


def test_latin_square_balancer_balances_value_counts():
    spec = _spec(subjects=3, predicates=2, objects=3, dates=4, values=4)
    random_examples, _ = plan_train_examples(
        spec, train_size=8, seed=0, strategy="random_baseline"
    )
    latin_examples, _ = plan_train_examples(
        spec, train_size=8, seed=0, strategy="latin_square_style_balanced"
    )
    random_std = statistics.pstdev(
        _field_count_vector(random_examples, spec, "value")
    )
    latin_std = statistics.pstdev(
        _field_count_vector(latin_examples, spec, "value")
    )
    assert latin_std < random_std


def test_coverage_verdict_logic():
    assert (
        tiny256_coverage_planner_verdict([])
        == "TINY256_COVERAGE_PLANNER = NO_COVERAGE_GAIN"
    )
    baseline = {
        "strategy": "random_baseline",
        "coverage_score": 1.0,
        "leakage_safe": True,
        "heldout_subject_object_value_seen_rate": 0.0,
        "heldout_subject_value_pair_seen_rate": 0.1,
        "heldout_object_value_pair_seen_rate": 0.1,
        "exact_heldout_sov_collision_count": 0,
        "exact_heldout_full_report_collision_count": 0,
        "value_class_count_std": 0.0,
        "train_size": 256,
    }
    marginal = {**baseline, "strategy": "pair_coverage_greedy", "coverage_score": 1.1}
    assert (
        tiny256_coverage_planner_verdict([baseline, marginal])
        == "TINY256_COVERAGE_PLANNER = MARGINAL_COVERAGE_GAIN"
    )
    value_pair = {
        **baseline,
        "strategy": "value_pair_coverage_greedy",
        "heldout_subject_value_pair_seen_rate": 0.9,
        "heldout_object_value_pair_seen_rate": 0.9,
        "coverage_score": 2.5,
    }
    assert (
        tiny256_coverage_planner_verdict([baseline, value_pair])
        == "TINY256_COVERAGE_PLANNER = VALUE_PAIR_COVERAGE_REPAIRED"
    )
    sov = {
        **baseline,
        "strategy": "sov_coverage_greedy_no_exact_heldout_overlap",
        "heldout_subject_object_value_seen_rate": 0.3,
        "coverage_score": 2.0,
    }
    assert (
        tiny256_coverage_planner_verdict([baseline, sov])
        == "TINY256_COVERAGE_PLANNER = SOV_COVERAGE_REPAIRED"
    )
    risky = {
        **baseline,
        "strategy": "heldout_aware",
        "leakage_safe": False,
        "heldout_subject_value_pair_seen_rate": 0.9,
        "exact_heldout_sov_collision_count": 10,
    }
    assert (
        tiny256_coverage_planner_verdict([risky])
        == "TINY256_COVERAGE_PLANNER = COVERAGE_REPAIRED_WITH_LEAKAGE_RISK"
    )


def test_manifest_artifact_schema(tmp_path: Path):
    out_dir = tmp_path / "coverage_planner"
    result = run_coverage_planner(
        schema_name="tiny_64",
        train_examples_list=[4],
        heldout_examples=4,
        seeds=[0],
        strategies=["random_baseline"],
        output_dir=out_dir,
    )
    assert "TINY256_COVERAGE_PLANNER" in result
    for relative in (
        "config.json",
        "coverage_strategy_table.csv",
        "coverage_strategy_metrics.json",
        "coverage_summary.md",
        "best_manifest_recommendations.json",
        "manifests/tiny64_train4_seed0_random_baseline.json",
        "coverage_matrices/subject_value_seen_random_baseline_4_0.csv",
        "coverage_matrices/object_value_seen_random_baseline_4_0.csv",
        "coverage_matrices/subject_object_value_seen_random_baseline_4_0.csv",
        "diagnostics/value_frequency_random_baseline_4_0.csv",
        "diagnostics/pair_coverage_random_baseline_4_0.csv",
        "diagnostics/triple_coverage_random_baseline_4_0.csv",
        "diagnostics/heldout_overlap_audit_random_baseline_4_0.json",
    ):
        assert (out_dir / relative).exists(), relative

"""Deterministic Curunir V6.7 terminal validation.

Exact nonpassing node IDs reproduced from accepted V6.6 are permitted as a
subset, while every new node, increased skip count, collection loss, focused
failure or reconstruction failure makes this command non-zero. The collection
floors were re-based on 2026-09-02 to the standalone suite; the earlier floors
stay accepted for reports already attached to campaign roots.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:  # direct script execution
    from reconstruct_v67 import PACKAGE_ROOT, REPO_ROOT, kernel_identity, reconstruct
except ModuleNotFoundError:  # import as tools.validate_v67 in tests/diagnostics
    from tools.reconstruct_v67 import PACKAGE_ROOT, REPO_ROOT, kernel_identity, reconstruct

BASELINE_PATH = PACKAGE_ROOT / "CURUNIR_V6_7_BASELINE_NONPASSING.json"
RECONSTRUCTION_PATH = PACKAGE_ROOT / "CURUNIR_V6_7_RECONSTRUCTION.json"
# Collection floors, newest first. This validator runs against the first set;
# a V6.7 report attached to a campaign root is accepted when it meets any set,
# so reports produced under the earlier floors keep verifying.
FLOOR_SETS = (
    {"since": "2026-09-02", "full_tests": 793, "full_skips": 0, "focused_tests": 131, "focused_skips": 0,
     "product_tests": 555, "product_skips": 0},
    {"since": "2026-08-25", "full_tests": 5676, "full_skips": 262, "focused_tests": 130, "focused_skips": 0,
     "product_tests": 630, "product_skips": 6},
)
MINIMUM_FULL_TESTS = FLOOR_SETS[0]["full_tests"]
MAXIMUM_FULL_SKIPS_WITHOUT_POSTGRES = FLOOR_SETS[0]["full_skips"]
MINIMUM_FOCUSED_TESTS = FLOOR_SETS[0]["focused_tests"]
MAXIMUM_FOCUSED_SKIPS = FLOOR_SETS[0]["focused_skips"]
MINIMUM_PRODUCT_TESTS = FLOOR_SETS[0]["product_tests"]
MAXIMUM_PRODUCT_SKIPS = FLOOR_SETS[0]["product_skips"]


def meets_floor(kind: str, tests: int, skipped: int) -> bool:
    """Whether a run's counts satisfy any accepted floor set for `kind`
    (full, focused or product)."""
    return any(tests >= floors[f"{kind}_tests"] and skipped <= floors[f"{kind}_skips"]
               for floors in FLOOR_SETS)

PRODUCT_PATTERNS = (
    "tests/test_analytic*.py",
    "tests/test_fabric*.py",
    "tests/test_semantic*.py",
    "tests/test_workbench*.py",
    "tests/test_operational_v3_*.py",
)
PRODUCT_FIXED = (
    "tests/test_operational_analytics_workflow.py",
    "tests/test_operational_association.py",
    "tests/test_operational_audit_hardening.py",
    "tests/test_operational_contracts.py",
    "tests/test_operational_projection_explain.py",
    "tests/test_operational_scenario.py",
    "tests/test_operational_schema_connectors.py",
    "tests/test_operational_sovereignty_pace.py",
    "tests/test_operational_store.py",
    "tests/test_operational_v2_fabric.py",
    "tests/test_operational_v2_integration.py",
)
PRODUCT_BASELINE_DESELECT = (
    "tests/test_operational_v2_integration.py::test_live_vs_synthetic_distinct",
)


class ValidationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run_pytest(label: str, arguments: list[str], environment: dict[str, str]) -> dict[str, object]:
    print(json.dumps({"stage": label, "status": "RUNNING"}), flush=True)
    handle = tempfile.NamedTemporaryFile(prefix=f"curunir-v67-{label}-", suffix=".xml", delete=False)
    junit = Path(handle.name)
    handle.close()
    try:
        completed = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "-rN", "--disable-warnings",
                "-c", "pytest.ini", f"--junitxml={junit}", *arguments,
            ],
            cwd=PACKAGE_ROOT,
            env=environment,
        )
        if completed.returncode != 0:
            raise ValidationError(f"{label} failed with exit code {completed.returncode}")
        result = _junit_result(junit)
        result.pop("nonpassing_nodeids")
        result.pop("nonpassing_outcomes")
        result["status"] = "PASSED"
        print(json.dumps({
            "stage": label,
            "status": "PASSED",
            "tests": result["tests"],
            "passed": result["passed"],
            "skipped": result["skipped"],
        }, sort_keys=True), flush=True)
        return result
    finally:
        junit.unlink(missing_ok=True)


def _junit_result(path: Path) -> dict[str, object]:
    suite = ET.parse(path).getroot().find("testsuite")
    if suite is None:
        raise ValidationError("full-suite JUnit has no testsuite")
    nonpassing: dict[str, str] = {}
    for case in suite.iter("testcase"):
        outcome = next(
            (child.tag for child in case if child.tag in {"failure", "error"}),
            None,
        )
        if outcome is not None:
            node = f"{case.attrib['classname']}::{case.attrib['name']}"
            nonpassing[node] = outcome
    ordered = sorted(nonpassing)
    return {
        "tests": int(suite.attrib["tests"]),
        "passed": int(suite.attrib["tests"])
        - int(suite.attrib["failures"])
        - int(suite.attrib["errors"])
        - int(suite.attrib["skipped"]),
        "skipped": int(suite.attrib["skipped"]),
        "failed": int(suite.attrib["failures"]),
        "errors": int(suite.attrib["errors"]),
        "nonpassing_nodeids": ordered,
        "nonpassing_outcomes": {
            node: nonpassing[node] for node in ordered
        },
        "nonpassing_node_set_sha256": hashlib.sha256(
            ("\n".join(ordered) + "\n").encode()
        ).hexdigest(),
        "junit_sha256": _sha256(path),
    }


def _preflight(kernel: Path) -> tuple[dict, dict, str]:
    if kernel.name != "argus":
        raise ValidationError("--kernel must name the argus package directory")
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ValidationError(f"terminal validation requires a clean tracked tree:\n{status}")
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    reconstruction = json.loads(RECONSTRUCTION_PATH.read_text(encoding="utf-8"))
    allowed = baseline["allowed_nonpassing_nodeids"]
    allowed_hash = hashlib.sha256(
        ("\n".join(sorted(allowed)) + "\n").encode()
    ).hexdigest()
    if allowed_hash != baseline["accepted_base_result"]["nonpassing_node_set_sha256"]:
        raise ValidationError("accepted-baseline node set does not match its pinned hash")
    allowed_errors = baseline.get("allowed_error_nodeids", [])
    if len(allowed_errors) != baseline["accepted_base_result"]["errors"] \
            or not set(allowed_errors).issubset(set(allowed)):
        raise ValidationError("accepted-baseline error-node classification is invalid")
    for path, expected in (
        (PACKAGE_ROOT / "tests" / "conftest.py", baseline["harness"]["tests_conftest_sha256"]),
        (PACKAGE_ROOT / "pytest.ini", baseline["harness"]["pytest_ini_sha256"]),
        (PACKAGE_ROOT / "requirements.txt", baseline["harness"]["requirements_sha256"]),
    ):
        if _sha256(path) != expected:
            raise ValidationError(f"validation harness identity mismatch: {path.name}")
    kernel_hash, kernel_files, _ = kernel_identity(kernel)
    if kernel_hash != reconstruction["external_kernel"]["tree_sha256"] \
            or kernel_files != reconstruction["external_kernel"]["python_file_count"]:
        raise ValidationError("external kernel does not match reconstruction identity")
    return baseline, reconstruction, commit


def _outcome_kind_changes(
    baseline: dict[str, object],
    current_outcomes: dict[str, str],
) -> list[str]:
    """Return current allowed nodes whose failure/error kind changed."""
    expected_errors = set(baseline["allowed_error_nodeids"])
    return sorted(
        node for node, outcome in current_outcomes.items()
        if outcome != ("error" if node in expected_errors else "failure")
    )


def validate(kernel: Path) -> dict[str, object]:
    baseline, _, commit = _preflight(kernel)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(kernel.parent)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["CURUNIR_ARGUS_KERNEL"] = str(kernel)

    focused_result = _run_pytest("focused_v67", ["tests/v67"], environment)
    if focused_result["tests"] < MINIMUM_FOCUSED_TESTS \
            or focused_result["skipped"] > MAXIMUM_FOCUSED_SKIPS:
        raise ValidationError(
            "focused V6.7 collection/skip gate failed: "
            f"tests={focused_result['tests']} skips={focused_result['skipped']}")

    product_files = sorted({
        path
        for pattern in PRODUCT_PATTERNS
        for path in glob.glob(str(PACKAGE_ROOT / pattern))
    } | {str(PACKAGE_ROOT / path) for path in PRODUCT_FIXED})
    product_arguments = [str(Path(path).relative_to(PACKAGE_ROOT)) for path in product_files]
    product_arguments.extend(
        f"--deselect={node}" for node in PRODUCT_BASELINE_DESELECT
    )
    product_result = _run_pytest("curunir_product_planes", product_arguments, environment)
    if product_result["tests"] < MINIMUM_PRODUCT_TESTS \
            or product_result["skipped"] > MAXIMUM_PRODUCT_SKIPS:
        raise ValidationError(
            "product-plane collection/skip gate failed: "
            f"tests={product_result['tests']} skips={product_result['skipped']}")

    with tempfile.TemporaryDirectory(prefix="curunir-v67-terminal-") as scratch:
        scratch_path = Path(scratch)
        reconstruction = reconstruct(
            kernel=kernel,
            output=scratch_path / "clean-checkout",
            ref=commit,
        )
        print(json.dumps({"stage": "clean_reconstruction", "status": "PASSED"}), flush=True)

        junit = scratch_path / "full-suite.xml"
        print(json.dumps({"stage": "full_repository", "status": "RUNNING"}), flush=True)
        full = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-rN",
                "-c",
                "pytest.ini",
                "-p",
                "no:cacheprovider",
                "--tb=no",
                "--disable-warnings",
                f"--junitxml={junit}",
            ],
            cwd=PACKAGE_ROOT,
            env=environment,
        )
        if full.returncode not in (0, 1):
            raise ValidationError(f"full repository execution aborted with {full.returncode}")
        full_result = _junit_result(junit)
        allowed = set(baseline["allowed_nonpassing_nodeids"])
        current = set(full_result["nonpassing_nodeids"])
        new_nonpassing = sorted(current - allowed)
        if new_nonpassing:
            raise ValidationError(
                "full suite has rewrite-only nonpassing nodes:\n" + "\n".join(new_nonpassing)
            )
        outcome_kind_changes = _outcome_kind_changes(
            baseline, full_result["nonpassing_outcomes"])
        if outcome_kind_changes:
            raise ValidationError(
                "accepted-baseline nodes changed failure/error kind:\n"
                + "\n".join(outcome_kind_changes)
            )
        if full_result["tests"] < MINIMUM_FULL_TESTS:
            raise ValidationError(
                f"full suite collection shrank to {full_result['tests']} tests"
            )
        if full_result["skipped"] > MAXIMUM_FULL_SKIPS_WITHOUT_POSTGRES:
            raise ValidationError(
                f"full suite skips increased to {full_result['skipped']}"
            )
        full_result["nonpassing_nodeids"] = sorted(current)
        full_result["rewrite_only_nonpassing"] = []
        full_result["outcome_kind_changes"] = []
        full_result["accepted_baseline_nonpassing_fixed"] = len(allowed - current)
        full_result["status"] = (
            "PASSED" if not current else "PASS_WITH_ACCEPTED_BASELINE_RESIDUALS"
        )
        print(json.dumps({
            "stage": "full_repository",
            "status": full_result["status"],
            "tests": full_result["tests"],
            "passed": full_result["passed"],
            "skipped": full_result["skipped"],
            "failed": full_result["failed"],
            "errors": full_result["errors"],
            "rewrite_only_nonpassing": [],
        }, sort_keys=True), flush=True)

    terminal_status = full_result["status"]
    return {
        "format": "curunir-v6.7-terminal-validation-v1",
        "status": terminal_status,
        "commit": commit,
        "focused_v67": focused_result,
        "curunir_product_planes": {
            **product_result,
            "deselected_accepted_baseline_nodes": list(PRODUCT_BASELINE_DESELECT),
        },
        "clean_reconstruction": reconstruction,
        "full_repository": full_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.kernel.resolve())
        if args.report is not None:
            report_path = args.report.resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = report_path.with_name(f".{report_path.name}.tmp")
            temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, report_path)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, ValidationError) as exc:
        print(f"V6_7_TERMINAL_REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

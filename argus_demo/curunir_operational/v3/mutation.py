"""Seven isolated source mutations with fail/restore/pass evidence."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..canonical import canonical_line


MUTATIONS = (
    {
        "id": "REMOVE_CAUSAL_VALIDATION", "file": "v3/node.py",
        "replacements": (
            ("if set(envelope.parent_event_ids) - self._event_ids:", "if False:  # MUTANT: missing parents accepted"),
            ("if required > known.get(node_id, 0) + 1:", "if False:  # MUTANT: origin sequence gaps accepted"),
            ("elif required > known.get(node_id, 0):", "elif False:  # MUTANT: causal context gaps accepted"),
        ),
        "test": "tests/test_operational_v3_distributed.py::test_out_of_order_delivery_retries_and_causal_gap_is_not_silent",
        "intended_failure": "out-of-order event no longer records CAUSAL_GAP",
    },
    {
        "id": "REPLACE_CONFLICT_WITH_LAST_WRITE_WINS", "file": "v3/merge.py",
        "replacements": (("if relation != CausalRelation.CONCURRENT:",
                          "if True:  # MUTANT: ignore every semantic concurrency conflict"),),
        "test": "tests/test_operational_v3_distributed.py::test_concurrent_status_conflict_no_last_write_wins_and_resolution_event",
        "intended_failure": "concurrent route values silently lack a conflict record",
    },
    {
        "id": "BYPASS_BUNDLE_ACCESS_FILTERING", "file": "v3/identity.py",
        "replacements": (("    record = marking.to_record() if isinstance(marking, AccessMarkingV3) else dict(marking)\n    roles = tuple(access_context.get(\"roles\", ()))",
                          "    return True  # MUTANT: transmit every marked record\n    record = marking.to_record() if isinstance(marking, AccessMarkingV3) else dict(marking)\n    roles = tuple(access_context.get(\"roles\", ()))"),),
        "test": "tests/test_operational_v3_distributed.py::test_access_filtering_before_bundle_and_hidden_count_token_size_stability",
        "intended_failure": "restricted subject and compartment enter partner bundle",
    },
    {
        "id": "ACCEPT_REVOKED_ACTOR_ACTION", "file": "v3/identity.py",
        "replacements": (("and not self.revoked_at(\"ACTOR\", actor_id, when)",
                          "and True  # MUTANT: revocation ignored"),),
        "test": "tests/test_operational_v3_distributed.py::test_actor_revocation_future_refusal_historical_validity",
        "intended_failure": "future action by revoked actor is accepted",
    },
    {
        "id": "REINVOKE_PROVIDER_DURING_REPLAY", "file": "v3/node.py",
        "replacements": (("\"provider_reinvocations\": 0,", "\"provider_reinvocations\": 1,  # MUTANT"),),
        "test": "tests/test_operational_v3_distributed.py::test_export_import_replay_preserves_distributed_history_without_provider",
        "intended_failure": "replay manifest reports a provider reinvocation",
    },
    {
        "id": "OMIT_SOURCE_DEPENDENCE_METADATA", "file": "v3/strategic.py",
        "replacements": (("return evidence_ref_from_bundle(bundle).to_record()",
                          "record = evidence_ref_from_bundle(bundle).to_record()\n    record[\"dependence_group_id\"] = None  # MUTANT\n    return record"),),
        "test": "tests/test_operational_v3_collaboration_strategic_operator.py::test_strategic_evidence_adapter_hypotheses_corrections_retractions_and_handoffs",
        "intended_failure": "dependent ARGUS evidence loses its dependence group",
    },
    {
        "id": "LAUNDER_ANNOTATION_AS_OPERATIONAL_STATE", "file": "v3/workflow.py",
        "replacements": (("payload={\"record_type\": \"annotation\", **record.to_record()},",
                          "payload={\"record_type\": \"route_status\", **record.to_record()},  # MUTANT"),),
        "test": "tests/test_operational_v3_collaboration_strategic_operator.py::test_annotation_is_not_accepted_operational_state",
        "intended_failure": "accepted analytical annotation materializes as route status",
    },
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_mutations(repository_argus_root: str | Path, output_path: str | Path) -> dict[str, Any]:
    repository_argus_root = Path(repository_argus_root).resolve()
    output_path = Path(output_path)
    production_hashes = {str(path.relative_to(repository_argus_root)): _digest(path)
                         for path in (repository_argus_root / "curunir_operational" / "v3").glob("*.py")}
    results = []
    with tempfile.TemporaryDirectory(prefix="curunir-v3-mutations-") as temporary:
        temp = Path(temporary)
        shutil.copytree(repository_argus_root / "curunir_operational", temp / "curunir_operational")
        (temp / "tests").mkdir()
        for name in ("test_operational_v3_distributed.py",
                     "test_operational_v3_collaboration_strategic_operator.py"):
            shutil.copyfile(repository_argus_root / "tests" / name, temp / "tests" / name)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((str(temp), str(repository_argus_root)))
        for mutation in MUTATIONS:
            path = temp / "curunir_operational" / mutation["file"]
            correct = path.read_text(encoding="utf-8")
            mutated = correct
            applied = []
            for old, new in mutation["replacements"]:
                count = mutated.count(old)
                if count != 1:
                    raise RuntimeError(f"mutation {mutation['id']} expected one source site, found {count}")
                mutated = mutated.replace(old, new, 1)
                applied.append({"old": old, "new": new})
            path.write_text(mutated, encoding="utf-8")
            failed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", mutation["test"]], cwd=temp,
                env=environment, text=True, capture_output=True, check=False)
            path.write_text(correct, encoding="utf-8")
            restored = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", mutation["test"]], cwd=temp,
                env=environment, text=True, capture_output=True, check=False)
            results.append({
                "mutation_id": mutation["id"], "file": f"curunir_operational/{mutation['file']}",
                "exact_change": applied, "test": mutation["test"],
                "intended_failure": mutation["intended_failure"],
                "mutant_returncode": failed.returncode,
                "mutant_caught": failed.returncode != 0,
                "mutant_output_tail": "\n".join((failed.stdout + failed.stderr).splitlines()[-12:]),
                "restored_returncode": restored.returncode,
                "restored_passed": restored.returncode == 0,
                "restored_source_hash": _digest(path),
            })
    production_unchanged = all(_digest(repository_argus_root / relative) == digest
                               for relative, digest in production_hashes.items())
    report = {
        "mutation_count": len(results), "caught_count": sum(item["mutant_caught"] for item in results),
        "restored_pass_count": sum(item["restored_passed"] for item in results),
        "production_sources_unchanged": production_unchanged, "results": results,
        "status": "PASS" if all(item["mutant_caught"] and item["restored_passed"] for item in results)
                             and production_unchanged else "FAIL",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(canonical_line(report) + "\n", encoding="utf-8")
    return report

"""Production repair freeze (contract Section 21).

After the freeze manifest is written, any hash drift in the frozen surface
invalidates the clean held-out evaluation.  Verification is cheap and must be
called at every post-freeze step.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from ..v4.io import read_json, write_json
from .models import now_utc, sha256

FREEZE_SCOPES = (
    "PRODUCTION_CODE", "CONFIGURATION", "PROMPTS", "THRESHOLDS",
    "ONTOLOGIES", "PACKET_BUILDER", "REPORT_TEMPLATES", "PARSER_CONFIGURATION",
)


def _file_hashes(paths: Iterable[str | Path]) -> dict[str, str]:
    output: dict[str, str] = {}
    for path in sorted(str(p) for p in paths):
        file_path = Path(path)
        if not file_path.is_file():
            raise ValueError(f"freeze target missing: {path}")
        output[path] = sha256(file_path.read_bytes())
    return output


def freeze_production(*, scope_paths: Mapping[str, Iterable[str | Path]],
                      thresholds: Mapping[str, Any], prompts: Mapping[str, str],
                      packet_builder_version: str, provider_versions: Mapping[str, str],
                      output_path: str | Path) -> dict[str, Any]:
    """Record the frozen production surface.  Refuses unknown scopes."""
    unknown = set(scope_paths) - set(FREEZE_SCOPES)
    if unknown:
        raise ValueError(f"unknown freeze scopes: {sorted(unknown)}")
    if "PRODUCTION_CODE" not in scope_paths:
        raise ValueError("a production freeze must include PRODUCTION_CODE")
    scopes = {scope: _file_hashes(paths) for scope, paths in sorted(scope_paths.items())}
    manifest = {
        "milestone": "CURUNIR_EPISTEMIC_CAPABILITY_CLOSURE_AND_CLEAN_GENERALIZATION_V5_1",
        "frozen_at": now_utc(),
        "scopes": scopes,
        "thresholds": dict(sorted(thresholds.items())),
        "thresholds_hash": sha256(dict(sorted(thresholds.items()))),
        "prompts_hash": sha256(dict(sorted(prompts.items()))),
        "prompts": dict(sorted(prompts.items())),
        "packet_builder_version": packet_builder_version,
        "provider_versions": dict(sorted(provider_versions.items())),
        "post_freeze_rule": (
            "No production logic, threshold, prompt, packet logic, evaluation "
            "policy, campaign-selection rule, report template, ontology, or "
            "parser configuration may change until the clean held-out "
            "evaluation completes.  Any change invalidates the run and "
            "requires a new versioned freeze plus three new investigations."),
    }
    manifest["integrity_hash"] = sha256(manifest)
    write_json(Path(output_path), manifest)
    return manifest


def verify_freeze(freeze_manifest_path: str | Path) -> dict[str, Any]:
    """Re-hash every frozen file; report drift.  Any drift is INVALID."""
    manifest = read_json(Path(freeze_manifest_path))
    recorded_integrity = manifest.get("integrity_hash")
    check = {key: value for key, value in manifest.items() if key != "integrity_hash"}
    if sha256(check) != recorded_integrity:
        return {"verdict": "INVALID", "reason": "FREEZE_MANIFEST_TAMPERED",
                "drifted_files": [], "verified_files": 0}
    drifted: list[dict[str, str]] = []
    verified = 0
    for scope, hashes in sorted(manifest["scopes"].items()):
        for path, recorded in sorted(hashes.items()):
            file_path = Path(path)
            current = sha256(file_path.read_bytes()) if file_path.is_file() else "MISSING"
            if current != recorded:
                drifted.append({"scope": scope, "path": path,
                                "recorded": recorded, "current": current})
            else:
                verified += 1
    return {
        "verdict": "PASS" if not drifted else "INVALID",
        "reason": "" if not drifted else "POST_FREEZE_MODIFICATION_DETECTED",
        "verified_files": verified, "drifted_files": drifted,
        "checked_at": now_utc(),
    }

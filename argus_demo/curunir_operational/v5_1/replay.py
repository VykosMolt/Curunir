"""Copied-root replay and portability (contract Section 29).

Custody is copied byte-identically with content paths remapped into the
copied root; the deterministic pipeline re-runs under a network guard that
blocks all socket connections and the acquisition transport; semantic register
hashes must reproduce exactly.
"""
from __future__ import annotations

import json
import shutil
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..v4.io import read_json, read_jsonl, write_json
from ..v4.models import sha256
from .campaign import analyze_campaign, build_reports
from .models import now_utc

# Volatile bookkeeping fields excluded from semantic register hashing; every
# substantive field participates.
_VOLATILE_FIELDS = frozenset({
    "creation_time", "recorded_time", "created_time", "generated_time",
    "admitted_time", "request_time", "response_time", "frozen_time",
    "built_time", "scored_time", "checked_at",
    # Location, not identity: the content hash is the semantic field.
    "content_path",
})


class ReplayNetworkViolation(RuntimeError):
    pass


class ReplayProviderViolation(RuntimeError):
    pass


class ReplayEscapeViolation(RuntimeError):
    pass


def copy_custody(campaign_custody_root: str | Path, replay_root: str | Path) -> dict[str, Any]:
    """Byte-identical custody copy with content paths remapped into the copy."""
    source_root = Path(campaign_custody_root).resolve()
    target_root = Path(replay_root).resolve()
    if source_root in target_root.parents or source_root == target_root:
        raise ReplayEscapeViolation("replay root may not nest inside the source custody")
    if target_root.exists() and any(target_root.iterdir()):
        raise ValueError("replay root must start empty")
    copied = verified = 0
    for path in sorted(source_root.rglob("*")):
        relative = path.relative_to(source_root)
        target = target_root / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if sha256(target.read_bytes()) != sha256(path.read_bytes()):
            raise ValueError(f"copy verification failed for {relative}")
        copied += 1

    records = target_root / "records" / "source_records.jsonl"
    remapped_rows = []
    for record in read_jsonl(records):
        content_path = Path(record["content_path"])
        try:
            relative = content_path.resolve().relative_to(source_root)
        except ValueError as exc:
            raise ReplayEscapeViolation(
                f"custody record resolves outside the copied root: {content_path}") from exc
        replay_path = target_root / relative
        if not replay_path.is_file():
            raise ValueError(f"copied content missing: {relative}")
        if sha256(replay_path.read_bytes()) != record["content_hash"]:
            raise ValueError(f"copied content hash mismatch: {relative}")
        verified += 1
        remapped_rows.append({**record, "content_path": str(replay_path)})
    records.write_text("".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for row in remapped_rows), encoding="utf-8")
    return {"files_copied": copied, "content_objects_verified": verified,
            "source_root": str(source_root), "replay_root": str(target_root)}


@contextmanager
def network_guard() -> Iterator[dict[str, int]]:
    """Seals the network during replay by blocking socket connections (which
    also blocks every stdlib HTTP path at its root) and the acquisition
    transport.  This module opens no network path of its own."""
    counters = {"network_attempts": 0, "provider_attempts": 0}

    def blocked_connect(self, *args: Any, **kwargs: Any) -> None:
        counters["network_attempts"] += 1
        raise ReplayNetworkViolation("network access is forbidden during replay")

    def blocked_transport(*args: Any, **kwargs: Any) -> None:
        counters["provider_attempts"] += 1
        raise ReplayProviderViolation("acquisition transport is forbidden during replay")

    from .. import canonical as operational_canonical
    from ..v4 import custody as v4_custody
    from . import campaign as v5_1_campaign
    originals = (
        (socket.socket, "connect", getattr(socket.socket, "connect")),
        (operational_canonical, "retrieve_public_bytes_v4",
         operational_canonical.retrieve_public_bytes_v4),
        (v4_custody, "transport_v4", v4_custody.transport_v4),
        (v4_custody, "acquire_public_source", v4_custody.acquire_public_source),
        (v5_1_campaign, "acquire_public_source", v5_1_campaign.acquire_public_source),
    )
    setattr(socket.socket, "connect", blocked_connect)
    operational_canonical.retrieve_public_bytes_v4 = blocked_transport  # type: ignore[assignment]
    v4_custody.transport_v4 = blocked_transport  # type: ignore[assignment]
    v4_custody.acquire_public_source = blocked_transport  # type: ignore[assignment]
    v5_1_campaign.acquire_public_source = blocked_transport  # type: ignore[assignment]
    try:
        yield counters
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _strip_volatile(item) for key, item in sorted(value.items())
                if key not in _VOLATILE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_strip_volatile(item) for item in value]
    return value


def semantic_register_hashes(analysis_root: str | Path,
                             report_root: str | Path | None = None) -> dict[str, str]:
    analysis = Path(analysis_root)
    hashes: dict[str, str] = {}
    for name in ("candidate_register.jsonl", "admission_register.jsonl",
                 "role_register.jsonl", "dependence_register.jsonl",
                 "claim_family_register.jsonl", "claim_support_register.jsonl",
                 "relation_register.jsonl", "capability_outcome_register.jsonl"):
        path = analysis / name
        if path.is_file():
            hashes[name] = sha256(_strip_volatile(read_jsonl(path)))
    for name in ("claim_register.json", "evidence_basis_register.json",
                 "source_register.json", "contradiction_register.json",
                 "hypothesis_register.json", "claim_dependence_states.json"):
        path = analysis / name
        if path.is_file():
            hashes[name] = sha256(_strip_volatile(read_json(path)))
    if report_root is not None:
        report = Path(report_root)
        for name in ("investigation_report.md", "investigation_report.txt"):
            path = report / name
            if path.is_file():
                hashes[name] = sha256(path.read_bytes())
        ledger = report / "proposition_evidence_ledger.jsonl"
        if ledger.is_file():
            hashes["proposition_evidence_ledger.jsonl"] = sha256(
                _strip_volatile(read_jsonl(ledger)))
    return hashes


def replay_campaign(*, replay_custody_root: str | Path, output_root: str | Path,
                    campaign_key: str, case: Mapping[str, Any],
                    original_analysis_root: str | Path,
                    original_report_root: str | Path) -> dict[str, Any]:
    """Re-run the deterministic pipeline over copied custody, zero network."""
    replay_root = Path(replay_custody_root).resolve()
    out = Path(output_root)
    for record in read_jsonl(replay_root / "records" / "source_records.jsonl"):
        content_path = Path(record["content_path"]).resolve()
        if replay_root not in content_path.parents:
            raise ReplayEscapeViolation(
                f"replay custody references content outside the copied root: {content_path}")
    with network_guard() as counters:
        analyze_campaign(replay_root, out / "analysis", campaign_key)
        build_reports(case, out / "analysis", out / "report")
    original = semantic_register_hashes(original_analysis_root, original_report_root)
    replayed = semantic_register_hashes(out / "analysis", out / "report")
    differences = sorted(
        name for name in set(original) | set(replayed)
        if original.get(name) != replayed.get(name))
    result = {
        "campaign_key": campaign_key,
        "network_calls": counters["network_attempts"],
        "provider_reinvocations": counters["provider_attempts"],
        "registers_compared": len(set(original) | set(replayed)),
        "hash_matches": len(set(original)) - len(differences),
        "differences": differences,
        "replayed_time": now_utc(),
    }
    write_json(out / "replay_result.json", result)
    return result


def portability_verdict(comparisons: Mapping[str, Mapping[str, Any]],
                        output_path: str | Path) -> dict[str, Any]:
    problems: list[str] = []
    for campaign_key, comparison in sorted(comparisons.items()):
        if comparison.get("network_calls"):
            problems.append(f"{campaign_key}: network used during replay")
        if comparison.get("provider_reinvocations"):
            problems.append(f"{campaign_key}: provider reinvoked during replay")
        if comparison.get("differences"):
            problems.append(f"{campaign_key}: register differences "
                            f"{comparison['differences']}")
    verdict = {
        "verdict": "PASS" if not problems else "INVALID",
        "problems": problems,
        "campaigns": {key: dict(value) for key, value in sorted(comparisons.items())},
        "decided_time": now_utc(),
    }
    write_json(Path(output_path), verdict)
    return verdict


def replay(**spec: Any) -> dict[str, Any]:
    """CLI wrapper (name fixed by cli.FORWARDED_COMMANDS)."""
    copy_result = copy_custody(spec["campaign_custody_root"], spec["replay_root"])
    comparison = replay_campaign(
        replay_custody_root=spec["replay_root"], output_root=spec["output_root"],
        campaign_key=spec["campaign_key"], case=spec["case"],
        original_analysis_root=spec["original_analysis_root"],
        original_report_root=spec["original_report_root"])
    return {"copy": copy_result, "comparison": comparison}

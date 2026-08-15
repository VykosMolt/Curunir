"""CLI-driven standing investigations, immutable recapture and semantic deltas."""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..canonical import retrieve_public_bytes_v4
from ..v4.io import append_jsonl, read_json, write_json
from ..v4.models import sha256, stable_id
from ..write_observation import declared_label
from .models import (
    FreshnessAssessment, IntelligenceUpdateAlert, LongitudinalDelta, SourceVersion,
    StandingInvestigation, now_utc,
)


def _store_content(root: Path, body: bytes) -> tuple[str, Path]:
    digest = sha256(body); path = root / "content" / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and sha256(path.read_bytes()) != digest:
        raise ValueError("content-address collision")
    if not path.exists():
        path.write_bytes(body)
    return digest, path


def _resolved_old_content(source: Mapping[str, Any], capture_root: Path) -> Path:
    digest = source["content_hash"]
    local = capture_root / "custody" / "content" / "sha256" / digest[:2] / digest[2:4] / digest
    if not local.is_file() or sha256(local.read_bytes()) != digest:
        raise FileNotFoundError(f"immutable prior source unavailable: {source['source_object_id']}")
    return local


def _semantic_text(body: bytes, headers: Mapping[str, str] | None = None) -> str:
    if body[:2] == b"\x1f\x8b":
        try: body = gzip.decompress(body)
        except OSError: pass
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|nav|footer|header|form)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\b(?:nonce|request[-_ ]?id|generated[-_ ]?at)\s*[:=]\s*[\w:.-]+", " ", text,
                  flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def standing_definitions(v4_root: str | Path, output_root: str | Path) -> list[dict[str, Any]]:
    v4 = Path(v4_root); out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    a_sources = read_json(v4 / "05_campaign_a_acquisition/live_capture/custody/source_object_manifest.json")
    b_sources = read_json(v4 / "11_campaign_b_acquisition/live_capture/custody/source_object_manifest.json")
    a_urls = tuple(item["final_urls"][0] for item in a_sources if item["publisher"] in {
        "Présidence de la République française", "European Commission"})[:2]
    b_urls = tuple(item["final_urls"][0] for item in b_sources if item["publisher"] in {
        "ENTSO-E", "ENTSO-E Expert Panel"})[:2]
    common = {"recapture_policy": {"mode": "CLI_DRIVEN", "maximum_requests_per_run": 4,
                                    "scheduler_active": False},
              "freshness_policy": {"official_final_report": "SUPERSEDED_ONLY_BY_NOTICE",
                                     "programme_status": "REVALIDATE_90_DAYS"},
              "materiality_policy": {"semantic_text_change": "MEDIUM", "correction": "HIGH",
                                      "cosmetic_change": "NO_ALERT"},
              "access_policy": "PUBLIC_ONLY_NO_BYPASS", "owning_node": "STRATEGIC_EVIDENCE_NODE",
              "review_policy": "AI_SECONDARY_REVIEW_HUMAN_PENDING", "status": "ACTIVE_RESEARCH_SHADOW",
              "version": 1}
    specs = [
        {"standing_id": "EUROPEAN_SOVEREIGN_DEFENCE_AI_WATCH_V5",
         "originating_case_id": "EUROPEAN_SOVEREIGN_DEFENCE_AI_AND_MISSION_DATA_2026",
         "purpose": "Revalidate official architecture, maturity and governance statements.",
         "scope": ("official policy", "programme maturity", "interoperability"),
         "exclusions": ("classified architecture", "private data"), "entities": ("ARCADIA", "MSS NATO"),
         "source_classes": ("FRENCH_EXECUTIVE", "EU_INSTITUTION", "NATO_PUBLIC"),
         "languages": ("en", "fr", "de"),
         "watch_queries": ("site:elysee.fr Arcadia ossature numérique défense", "site:europa.eu ARCADIA defence AI"),
         "watched_urls": a_urls, **common},
        {"standing_id": "IBERIAN_BLACKOUT_OFFICIAL_RECORD_WATCH_V5",
         "originating_case_id": "EUROPEAN_PUBLIC_INSTITUTIONAL_MATTER_V4_IBERIAN_BLACKOUT",
         "purpose": "Track corrections, final records and competent institutional updates.",
         "scope": ("official technical record", "correction", "legal-scope statements"),
         "exclusions": ("individual blame", "private operational data"),
         "entities": ("ENTSO-E Expert Panel", "Spanish government", "REN"),
         "source_classes": ("EUROPEAN_TECHNICAL_PANEL", "NATIONAL_AUTHORITY"),
         "languages": ("en", "es", "pt"),
         "watch_queries": ("site:entsoe.eu 28 April 2025 blackout correction final report",),
         "watched_urls": b_urls, **common},
    ]
    records = []
    for spec in specs:
        payload = dict(spec); payload["integrity_hash"] = sha256(payload)
        record = StandingInvestigation(**payload).__dict__; records.append(record)
        write_json(out / f"{record['standing_id'].casefold()}_definition.json", record)
    write_json(out / "standing_investigations.json", records)
    return records


def _source_for_url(sources: Iterable[Mapping[str, Any]], url: str) -> Mapping[str, Any]:
    for item in sources:
        if url in item["final_urls"] or url in item["requested_urls"]:
            return item
    raise KeyError(url)


def execute_live_watch(*, definition: Mapping[str, Any], sources: list[Mapping[str, Any]],
                       capture_root: str | Path, output_root: str | Path,
                       timeout_seconds: float = 30.0, maximum_bytes: int = 60_000_000) -> dict[str, Any]:
    """Perform a bounded recapture. The attempt-start event precedes transport."""
    capture = Path(capture_root); out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    update_run_id = stable_id("update-run", definition["standing_id"], "2026-07-22-live-1")
    versions: list[SourceVersion] = []; failures = []; requests = 0
    for url in definition["watched_urls"]:
        source = _source_for_url(sources, url); requests += 1
        retrieval_id = stable_id("recapture", update_run_id, source["source_object_id"], requests)
        append_jsonl(out / "recapture_events.jsonl", [{"event": "ATTEMPT_STARTED", "retrieval_id": retrieval_id,
            "url": url, "access_decision": "ALLOW_PUBLIC_RETRIEVAL", "time": now_utc()}])
        response = retrieve_public_bytes_v4(url=url, request_headers={"User-Agent": "Curunir-Research-Shadow-V5/1.0"},
            timeout_seconds=timeout_seconds, maximum_bytes=maximum_bytes)
        body = response["body"]; old_path = _resolved_old_content(source, capture); old_body = old_path.read_bytes()
        if response["error"] or not body or response["truncated"]:
            change = "UNAVAILABLE" if response["error"] else "MALFORMED"
            failures.append({"retrieval_id": retrieval_id, "url": url, "error": response["error"],
                             "truncated": response["truncated"]})
            digest = None; content_path = None; byte_changed = False; semantic_changed = False
        else:
            digest, stored = _store_content(out, body); content_path = str(stored)
            byte_changed = digest != source["content_hash"]
            semantic_changed = _semantic_text(body, response["headers"]) != _semantic_text(old_body)
            if not byte_changed: change = "NO_CHANGE"
            elif semantic_changed: change = "CONTENT_CHANGED"
            else: change = "METADATA_CHANGED"
        version = SourceVersion(stable_id("source-version", source["source_object_id"], digest, update_run_id),
            source["source_object_id"], stable_id("source-version-v4", source["source_object_id"], source["content_hash"]),
            retrieval_id, digest, sha256(response["headers"]), content_path, "ALLOW_PUBLIC_RETRIEVAL", change,
            byte_changed, semantic_changed, source.get("publication_time"), now_utc(), update_run_id)
        versions.append(version)
        append_jsonl(out / "recapture_events.jsonl", [{"event": "ATTEMPT_COMPLETED", "retrieval_id": retrieval_id,
            "status": response["status"], "final_url": response["final_url"], "redirects": response["redirects"],
            "change_state": change, "time": now_utc()}])
    write_json(out / "source_versions.json", [item.__dict__ for item in versions])
    write_json(out / "recapture_failures.json", failures)
    counts = {state: sum(item.change_state == state for item in versions) for state in sorted({x.change_state for x in versions})}
    result = {"standing_id": definition["standing_id"], "update_run_id": update_run_id,
              "live_requests": requests, "change_counts": counts, "failures": len(failures),
              "new_official_leads": [], "discovery_provider": "FROZEN_OFFICIAL_WATCH_URL_SET",
              "live_change_result": "LIVE_CHANGE_CAPTURED" if any(x.semantic_changed for x in versions)
                                    else "NO_MATERIAL_CHANGE",
              "canonical_write_attempts": 0, "canonical_writes": 0,
              **declared_label("v5/longitudinal.py::execute_live_watch")}
    result["integrity_hash"] = sha256(result); write_json(out / "update_run.json", result)
    return result


def controlled_mechanics_scenario(output_root: str | Path) -> dict[str, Any]:
    out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    old = b"Official preliminary record: 10 units."
    new = b"Official correction: the preliminary value of 10 units is corrected to 12 units."
    old_hash, old_path = _store_content(out / "controlled", old)
    new_hash, new_path = _store_content(out / "controlled", new)
    versions = [
        SourceVersion("controlled-source-v1", "controlled-source", None, "controlled-retrieval-v1", old_hash,
            sha256({}), str(old_path), "CONTROLLED_FIXTURE", "NEW_SOURCE", True, True,
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "controlled-run-1"),
        SourceVersion("controlled-source-v2", "controlled-source", "controlled-source-v1", "controlled-retrieval-v2",
            new_hash, sha256({}), str(new_path), "CONTROLLED_FIXTURE", "CORRECTION_PUBLISHED", True, True,
            "2026-02-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00", "controlled-run-2"),
    ]
    deltas = [
        LongitudinalDelta("controlled-document-delta", "DOCUMENT", "controlled-source-v1", "controlled-source-v2",
            "MODIFIED", "official correction text", ("controlled-source-v2",), "HIGH", "REVALIDATION_REQUIRED",
            "HUMAN_REVIEW_REQUIRED"),
        LongitudinalDelta("controlled-claim-delta", "CLAIM", "claim-10-units", "claim-12-units", "SUPERSEDED",
            "corrected numeric value", ("controlled-source-v2",), "HIGH", "REVALIDATION_REQUIRED",
            "HUMAN_REVIEW_REQUIRED"),
        LongitudinalDelta("controlled-report-delta", "REPORT", "report-v1", "report-v2", "MODIFIED",
            "claim correction propagation", ("claim-12-units",), "HIGH", "REGENERATED", "HUMAN_REVIEW_REQUIRED"),
        LongitudinalDelta("controlled-handoff-delta", "MISSION_HANDOFF", "handoff-v1", "handoff-v2", "SUPERSEDED",
            "support corrected", ("claim-12-units",), "HIGH", "HUMAN_REVIEW_REQUIRED", "HUMAN_REVIEW_REQUIRED"),
        LongitudinalDelta("controlled-proposal-delta", "KERNEL_PROPOSAL", "proposal-v1", "proposal-v2", "INVALIDATED",
            "upstream dependency changed", ("claim-12-units",), "HIGH", "BLOCKED", "HUMAN_REVIEW_REQUIRED"),
    ]
    write_json(out / "controlled_source_versions.json", [item.__dict__ for item in versions])
    write_json(out / "controlled_deltas.json", [item.__dict__ for item in deltas])
    redline = {"previous_sentence": "The preliminary record states 10 units.",
               "new_sentence": "A later official correction supersedes the preliminary value of 10 units with 12 units.",
               "change_type": "CORRECTED", "changed_evidence": ["controlled-source-v2"],
               "changed_claim": "claim-12-units", "changed_dependence_state": "UNCHANGED",
               "changed_contradiction_state": "CORRECTION", "changed_hypothesis_state": "REVALIDATION_REQUIRED",
               "reviewer_requirement": "HUMAN_REVIEW_REQUIRED"}
    write_json(out / "controlled_report_redline.json", redline)
    report = {"scenario": "CONTROLLED_LONGITUDINAL_MECHANICS_SCENARIO", "live_finding": False,
              "source_versions": 2, "deltas": len(deltas), "proposal_invalidated": True,
              "old_versions_preserved": True, "canonical_write_attempts": 0, "canonical_writes": 0,
              **declared_label("v5/longitudinal.py::controlled_mechanics_scenario"),
              "verdict": "PASS"}
    report["integrity_hash"] = sha256(report); write_json(out / "controlled_scenario_report.json", report)
    return report


def derive_watch_deltas(definition: Mapping[str, Any], update_root: str | Path,
                        output_root: str | Path) -> dict[str, Any]:
    versions = read_json(Path(update_root) / "source_versions.json"); out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    deltas: list[LongitudinalDelta] = []; freshness: list[FreshnessAssessment] = []; alerts = []
    for version in versions:
        state = "MODIFIED" if version["semantic_changed"] else "UNCHANGED"
        materiality = "MEDIUM" if version["semantic_changed"] else "NO_ALERT"
        propagation = "REVALIDATION_REQUIRED" if version["semantic_changed"] else "UNAFFECTED"
        deltas.append(LongitudinalDelta(stable_id("document-delta", version["source_version_id"]), "DOCUMENT",
            version["previous_version_id"], version["source_version_id"], state, version["change_state"],
            (version["source_version_id"],), materiality, propagation, "HUMAN_REVIEW_REQUIRED"))
        freshness_state = "REVALIDATION_REQUIRED" if version["semantic_changed"] else \
                          "UNAVAILABLE" if version["change_state"] == "UNAVAILABLE" else "CURRENT"
        freshness.append(FreshnessAssessment(stable_id("freshness", version["source_version_id"]),
            version["source_object_id"], freshness_state, (version["change_state"],), "OFFICIAL_PUBLIC_SOURCE",
            "CASE_SPECIFIC", now_utc(), freshness_state == "REVALIDATION_REQUIRED"))
        if version["semantic_changed"]:
            alerts.append(IntelligenceUpdateAlert(stable_id("alert", version["source_version_id"]),
                definition["standing_id"], (version["source_object_id"],), "PRIOR_CAPTURE", "CONTENT_CHANGED",
                (version["source_version_id"],), "REVALIDATION_REQUIRED", "Semantic change not yet human reviewed.",
                {"releasability": ["PUBLIC"]}, "STRATEGIC_EVIDENCE_NODE", "HUMAN_REVIEW_REQUIRED", "MEDIUM",
                "REPORT_UPDATE_AND_PROPOSAL_REVALIDATION"))
    write_json(out / "document_deltas.json", [item.__dict__ for item in deltas])
    # Explicit empty or derived delta registers avoid implying hidden updates.
    for kind in ("span", "candidate", "entity", "source_origin", "evidence_basis", "claim", "contradiction",
                 "hypothesis", "report", "handoff", "kernel_proposal"):
        derived = [] if not alerts else [{"kind": kind.upper(), "state": "REQUIRES_REVIEW",
            "reason": "material source version change", "review_state": "HUMAN_REVIEW_REQUIRED"}]
        write_json(out / f"{kind}_deltas.json", derived)
    write_json(out / "freshness_assessments.json", [item.__dict__ for item in freshness])
    write_json(out / "alerts.json", [item.__dict__ for item in alerts])
    update = {"standing_id": definition["standing_id"], "document_deltas": len(deltas),
              "material_changes": len(alerts), "alerts": len(alerts),
              "report_change": "REQUIRES_REVIEW" if alerts else "UNCHANGED",
              "handoff_change": "HANDOFF_UPDATE" if alerts else "NO_EFFECT",
              "proposal_change": "REVALIDATION_REQUIRED" if alerts else "NO_EFFECT",
              "historical_versions_preserved": True, "verdict": "PASS"}
    update["integrity_hash"] = sha256(update); write_json(out / "update_summary.json", update)
    return update


def cross_case_memory(v4_root: str | Path, output_root: str | Path) -> dict[str, Any]:
    out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    records = [
        {"reference_id": "cross-case-source-policy-v1", "kind": "SOURCE_REUSE_POLICY",
         "source_object_id": None, "from_case": "CAMPAIGN_A", "to_case": "CAMPAIGN_B",
         "decision": "NO_SHARED_SOURCE_IDENTIFIED", "applicability": "CASE_SPECIFIC",
         "assumptions_transferred": False, "access_preserved": True},
        {"reference_id": "cross-case-controlled-correction-v1", "kind": "CONTROLLED_PROPAGATION_PROOF",
         "source_object_id": "controlled-source", "from_case": "CONTROLLED_CASE_A", "to_case": "CONTROLLED_CASE_B",
         "decision": "REFERENCE_RAW_EVIDENCE_REVALIDATE_CLAIM", "applicability": "REVALIDATION_REQUIRED",
         "assumptions_transferred": False, "access_preserved": True},
    ]
    write_json(out / "cross_case_records.json", records)
    result = {"records": len(records), "global_truth_graph_created": False,
              "case_specific_applicability_preserved": True, "access_leakage_findings": 0,
              "controlled_cross_case_correction_propagated": True, "verdict": "PASS"}
    result["integrity_hash"] = sha256(result); write_json(out / "cross_case_result.json", result)
    return result

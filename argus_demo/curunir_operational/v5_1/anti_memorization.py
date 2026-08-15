"""Anti-memorization audit (contract Section 20).

Scans V5.1-owned material for case-specific answer encoding.  The scanner
itself must remain campaign-agnostic: the sensitive-term manifest is BUILT AT
SCAN TIME from the frozen campaign artifacts, never hardcoded here.  Nothing
in this module may name a campaign, source, entity, or report sentence.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..v4.io import read_json, read_jsonl, write_json
from .models import sha256, stable_id

OCCURRENCE_CLASSES = (
    "LEGITIMATE_GENERIC_REFERENCE",
    "TEST_FIXTURE_ONLY",
    "DOCUMENTED_SOURCE_SPECIFIC_RULE",
    "SUSPICIOUS_CASE_ENCODING",
    "FORBIDDEN_HELDOUT_LEAKAGE",
)

# Generic vocabulary that legitimately appears in production code and must not
# be treated as campaign-specific even when it also appears in campaign
# artifacts (ontology labels, ISO codes, common technical words).
_GENERIC_STOPLIST = {
    "public", "official", "report", "source", "publication", "translation",
    "mirror", "archive", "correction", "retraction", "supersession", "update",
    "claim", "evidence", "independent", "dependence", "unknown", "review",
    "european", "commission", "parliament", "regulation", "directive",
    "government", "ministry", "agency", "press", "release", "article", "annex",
}


@dataclass(frozen=True)
class SensitiveTerm:
    term: str
    term_kind: str          # CAMPAIGN_NAME | SOURCE_URL | SOURCE_DOMAIN |
                            # REPORT_SENTENCE | PACKET_ID | ENTITY_NAME |
                            # HELDOUT_TERM
    origin_artifact: str


def _clean_terms(values: Iterable[tuple[str, str, str]]) -> tuple[SensitiveTerm, ...]:
    output: dict[tuple[str, str], SensitiveTerm] = {}
    for term, kind, origin in values:
        term = term.strip()
        if len(term) < 6 or term.casefold() in _GENERIC_STOPLIST:
            continue
        output[(term.casefold(), kind)] = SensitiveTerm(term, kind, origin)
    return tuple(output.values())


def build_term_manifest(campaign_roots: Mapping[str, str | Path],
                        heldout_roots: Mapping[str, str | Path] | None = None) -> dict[str, Any]:
    """Extract campaign-specific terms from frozen artifacts at scan time.

    ``campaign_roots`` maps a label to an artifact root that contains source
    manifests (``source_object_manifest.json`` / ``source_records.jsonl``) and
    report files (``investigation_report.md`` / sentence ledgers).  Held-out
    roots are separated: their terms classify as FORBIDDEN_HELDOUT_LEAKAGE.
    """
    collected: list[tuple[str, str, str]] = []

    def harvest(label: str, root: Path, heldout: bool) -> None:
        kind_prefix = "HELDOUT_TERM" if heldout else None
        for manifest in sorted(root.rglob("source_object_manifest.json")):
            for record in read_json(manifest):
                title = str(record.get("title") or "")
                if title:
                    collected.append((title, kind_prefix or "ENTITY_NAME", str(manifest)))
                for url in tuple(record.get("requested_urls") or ()) + tuple(record.get("final_urls") or ()):
                    collected.append((url, kind_prefix or "SOURCE_URL", str(manifest)))
                    domain = re.sub(r"^https?://([^/]+).*$", r"\1", url)
                    collected.append((domain, kind_prefix or "SOURCE_DOMAIN", str(manifest)))
        for records in sorted(root.rglob("source_records.jsonl")):
            for record in read_jsonl(records):
                for url in tuple(record.get("requested_urls") or ()):
                    collected.append((url, kind_prefix or "SOURCE_URL", str(records)))
        for report in sorted(root.rglob("investigation_report.md")):
            for line in report.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if len(line) > 60 and not line.startswith("#"):
                    collected.append((line, kind_prefix or "REPORT_SENTENCE", str(report)))
        for ledger in sorted(root.rglob("*packet*.jsonl")):
            for record in read_jsonl(ledger):
                packet_id = record.get("packet_id")
                if packet_id:
                    collected.append((str(packet_id), kind_prefix or "PACKET_ID", str(ledger)))

    for label, root in sorted(campaign_roots.items()):
        harvest(label, Path(root), heldout=False)
    for label, root in sorted((heldout_roots or {}).items()):
        harvest(label, Path(root), heldout=True)

    terms = _clean_terms(collected)
    manifest = {
        "term_count": len(terms),
        "terms": [term.__dict__ for term in terms],
        "campaign_roots": {key: str(value) for key, value in sorted(campaign_roots.items())},
        "heldout_roots": {key: str(value) for key, value in sorted((heldout_roots or {}).items())},
    }
    manifest["integrity_hash"] = sha256(manifest)
    return manifest


def _classify(path: Path, term: SensitiveTerm,
              documented_rules: Mapping[str, str]) -> str:
    if term.term_kind == "HELDOUT_TERM":
        return "FORBIDDEN_HELDOUT_LEAKAGE"
    text_path = str(path)
    if term.term.casefold() in {key.casefold() for key in documented_rules}:
        return "DOCUMENTED_SOURCE_SPECIFIC_RULE"
    if "/tests/" in text_path or text_path.rsplit("/", 1)[-1].startswith("test_"):
        return "TEST_FIXTURE_ONLY"
    if "/artifacts/" in text_path:
        return "LEGITIMATE_GENERIC_REFERENCE"
    return "SUSPICIOUS_CASE_ENCODING"


def scan_paths(target_paths: Iterable[str | Path], term_manifest: Mapping[str, Any],
               documented_rules: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Scan target files for sensitive terms and classify every occurrence."""
    documented = dict(documented_rules or {})
    terms = tuple(SensitiveTerm(**value) for value in term_manifest["terms"])
    occurrences: list[dict[str, Any]] = []
    scanned: list[str] = []
    for target in sorted(str(p) for p in target_paths):
        path = Path(target)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        scanned.append(target)
        folded = text.casefold()
        for term in terms:
            needle = term.term.casefold()
            position = folded.find(needle)
            while position >= 0:
                line = text.count("\n", 0, position) + 1
                classification = _classify(path, term, documented)
                occurrences.append({
                    "occurrence_id": stable_id("memo-occurrence", target, term.term, position),
                    "path": target, "line": line, "term": term.term,
                    "term_kind": term.term_kind, "origin_artifact": term.origin_artifact,
                    "classification": classification,
                })
                position = folded.find(needle, position + 1)
    by_class = {name: sum(item["classification"] == name for item in occurrences)
                for name in OCCURRENCE_CLASSES}
    verdict = "PASS_HARDENED"
    if by_class["SUSPICIOUS_CASE_ENCODING"]:
        verdict = "BLOCKED_SUSPICIOUS_CASE_ENCODING"
    if by_class["FORBIDDEN_HELDOUT_LEAKAGE"]:
        verdict = "INVALID_FORBIDDEN_HELDOUT_LEAKAGE"
    report = {
        "files_scanned": len(scanned), "scanned_paths": scanned,
        "term_manifest_hash": term_manifest.get("integrity_hash"),
        "occurrences": occurrences, "occurrences_by_class": by_class,
        "documented_rules": documented, "verdict": verdict,
    }
    report["integrity_hash"] = sha256(report)
    return report


def write_audit(report: Mapping[str, Any], output_path: str | Path) -> None:
    write_json(Path(output_path), dict(report))

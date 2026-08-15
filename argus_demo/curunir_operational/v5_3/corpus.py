"""Blinded dossier construction (contract Sections 5.2, 7.2, 16).

Evaluation code.  It is on ``provenance.EVALUATOR_MODULES``, so nothing here
can mint a production prediction even by importing ``record_prediction``.

Two rules shape every builder below.

* A dossier is built from raw evidence and normalized observations only.  It
  never reads the prediction manifest, and the sealed answer is not passed in.
* A normalized observation is rendered as what was seen and where, never as
  what it implies.  V5.2 shipped ``issuing_institution: European Commission``
  and all three reviewers read it as the answer; the same evidence now renders
  as an observation of type, value and provenance with the semantic role
  explicitly withheld.

Research shadow only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..v4.io import write_json
from ..v5_1.models import now_utc, sha256
from ..v5_2 import dossiers as v52_dossiers
from ..v5_2.dossiers import Dossier, certify_sufficiency
from .provenance import NormalizedObservationRecord, RawEvidenceRecord

REVIEWER_WITHHELD = "not supplied to reviewer"


def render_observation(observation: NormalizedObservationRecord,
                       raw_by_id: Mapping[str, RawEvidenceRecord] | None = None
                       ) -> dict[str, Any]:
    """Render one observation for a reviewer without naming a role.

    The shape is deliberate: a reviewer sees the observed string, what kind of
    observation it is, where it came from, how strong it is, and an explicit
    statement that the semantic role has been withheld.
    """
    raw = (raw_by_id or {})
    excerpts = []
    for evidence_id in observation.raw_evidence_ids:
        record = raw.get(evidence_id)
        if record is not None:
            excerpts.append({"raw_evidence_id": evidence_id,
                             "excerpt": record.raw_text,
                             "locator": dict(record.locator)})
        else:
            excerpts.append({"raw_evidence_id": evidence_id})
    return {
        "observed_value": observation.observed_value,
        "observation_type": observation.observation_type,
        "observation_source": observation.normalization,
        "explicit_or_inferred": observation.mode,
        "evidence_strength": observation.strength,
        "provenance": dict(observation.provenance),
        "raw_support": excerpts,
        "semantic_role": REVIEWER_WITHHELD,
    }


def _absent(marker: str = "NOT_PRESENT", checked: str = "looked for, not found"):
    return {"state": marker, "checked": checked}


def _sections(kind: str, supplied: Mapping[str, Any]) -> dict[str, Any]:
    sections = {name: _absent() for name in v52_dossiers.REQUIRED_SECTIONS[kind]}
    for name, value in supplied.items():
        sections[name] = value if value not in (None, "", [], {}) else _absent()
    return sections


def build_source_dossier(*, subject_id: str, role_asked: str,
                         observations: Sequence[NormalizedObservationRecord],
                         raw_by_id: Mapping[str, RawEvidenceRecord],
                         url: str | None = None,
                         redirect_chain: Any = None) -> Dossier:
    """A source dossier that shows observations, not conclusions."""
    rendered = [render_observation(o, raw_by_id) for o in observations]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for observation, payload in zip(observations, rendered):
        by_type.setdefault(observation.observation_type, []).append(payload)

    def group(*types: str) -> Any:
        found = [item for kind in types for item in by_type.get(kind, ())]
        return found or _absent()

    sections = _sections("SOURCE_DOSSIER", {
        "manifestation": group("DOCUMENT_IDENTIFIER", "HEADER_TEXT"),
        "url": url or _absent(),
        "redirect_chain": redirect_chain or _absent("NO_REDIRECTS"),
        "domain": group("DOMAIN_HOST"),
        "title_page": group("TITLE_PAGE_INSTITUTION", "HEADER_TEXT"),
        "header_and_footer": group("HEADER_TEXT", "FOOTER_TEXT"),
        "author_lines": group("EXPLICIT_AUTHOR_LINE", "EXPLICIT_EDITOR_LINE"),
        "publication_metadata": group("PUBLICATION_DATE", "EXPLICIT_PUBLISHER_LINE",
                                      "COPYRIGHT_HOLDER"),
        "document_series": group("DOCUMENT_SERIES_OWNER"),
        "hosting_institution": group("DOMAIN_HOST", "REDIRECT_CHAIN"),
        "issuing_institution": group("EXPLICIT_ISSUER_LINE",
                                     "INSTITUTIONAL_ATTRIBUTION"),
        "submitted_by": group("EXPLICIT_SUBMISSION_LINE"),
        "ownership_evidence": group("OWNERSHIP_RECORD", "OPERATING_ENTITY_NOTICE"),
        "archive_or_mirror_relations": group("ARCHIVE_PROVIDER", "MIRROR_NOTICE"),
        "organization_and_programme_records": group("INSTITUTIONAL_ATTRIBUTION"),
        "timestamps": group("PUBLICATION_DATE"),
        "explicit_source_role_evidence": group(
            "EXPLICIT_PUBLISHER_LINE", "EXPLICIT_ISSUER_LINE",
            "TRANSLATION_NOTICE", "SYNDICATION_NOTICE", "COMMISSIONING_NOTICE",
            "REVISION_STATEMENT"),
    })
    options = sorted(set(v52_dossiers.__dict__.get("SOURCE_ROLE_OPTIONS", ())) or {
        "AUTHORED_BY", "EDITED_BY", "SUBMITTED_BY", "ISSUED_BY", "PUBLISHED_BY",
        "HOSTED_BY", "MIRRORED_BY", "ARCHIVED_BY", "TRANSLATED_BY",
        "SYNDICATED_BY", "COMMISSIONED_BY", "OWNED_BY", "OPERATED_BY",
        "NO_ROLE_ESTABLISHED"})
    return v52_dossiers.build_dossier(
        kind="SOURCE_DOSSIER",
        decision_question=(
            f"On the observations in this dossier, which role does the named "
            f"institution hold over this publication? Answer NO_ROLE_ESTABLISHED "
            f"if the observations do not establish one."),
        decision_options=options, sections=sections)


def build_span_dossier(*, candidate: Mapping[str, Any], document_text: str,
                       document_language: str, parser: str,
                       alternatives: int, boundary_warnings: Sequence[str],
                       recorded_fields: Mapping[str, Any]) -> Dossier:
    start, end = int(candidate["span_start"]), int(candidate["span_end"])
    sections = _sections("SPAN_DOSSIER", {
        "original_bytes_reference": {"source_object_id": candidate["source_object_id"],
                                     "span": [start, end], "parser": parser},
        "normalized_text": candidate["original_text"],
        "local_context": document_text[max(0, start - 300):min(len(document_text), end + 300)],
        "expanded_context": document_text[max(0, start - 1200):min(len(document_text), end + 1200)],
        "layout_metadata": {"page_or_section": candidate.get("page_or_section"),
                            "mapping_precision": candidate.get("mapping_precision"),
                            "parser": parser},
        "candidate_proposition": dict(recorded_fields),
        "alternative_boundaries": {"boundaries_considered": alternatives + 1,
                                   "boundary_warnings": list(boundary_warnings)},
        "original_language": document_language,
    })
    return v52_dossiers.build_dossier(
        kind="SPAN_DOSSIER",
        decision_question=("What should happen to this extraction candidate: "
                           "should it be accepted, does it need more surrounding "
                           "context, is its mapping precision insufficient, is it "
                           "page furniture, or can it not carry its recorded type?"),
        decision_options=["ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED",
                          "EVIDENCE_BOUND", "QUARANTINED", "REJECTED"],
        sections=sections,
        original_language_material={"language": document_language,
                                    "text": candidate["original_text"]},
        translation_material={"state": "NOT_APPLICABLE"})


def build_pair_dossier(*, kind: str, decision_question: str,
                       decision_options: Sequence[str],
                       sections: Mapping[str, Any]) -> Dossier:
    return v52_dossiers.build_dossier(
        kind=kind, decision_question=decision_question,
        decision_options=list(decision_options),
        sections=_sections(kind, sections))


# ---------------------------------------------------------------------------
# Corpus sealing
# ---------------------------------------------------------------------------

def seal(*, dossiers: Sequence[Dossier], predictions: Mapping[str, Any],
         dossier_to_object: Mapping[str, str], strata: Mapping[str, str],
         output_root: str | Path, reserve_fraction: float = 0.25,
         seed: int = 20260725) -> dict[str, Any]:
    """Certify, split and seal.  Refuses to seal a unit with no prediction."""
    import random

    root = Path(output_root)
    (root / "corpus").mkdir(parents=True, exist_ok=True)
    (root / "sealed_keys").mkdir(parents=True, exist_ok=True)

    certificates, admissible, defects, orphans = [], [], [], []
    for dossier in dossiers:
        certificate = certify_sufficiency(dossier)
        certificates.append(certificate.to_record())
        object_id = dossier_to_object.get(dossier.dossier_id)
        if certificate.result == "CONSTRUCTION_DEFECT":
            defects.append(dossier.dossier_id)
        elif object_id is None or object_id not in predictions:
            # Section 16.3: a unit with no frozen production prediction may not
            # be scored, so it may not be sealed into the scored corpus either.
            orphans.append(dossier.dossier_id)
        else:
            admissible.append(dossier)

    by_surface: dict[str, list[Dossier]] = {}
    for dossier in admissible:
        by_surface.setdefault(dossier.surface, []).append(dossier)

    primary: list[Dossier] = []
    reserve: list[Dossier] = []
    for surface, items in sorted(by_surface.items()):
        ordered = sorted(items, key=lambda d: d.dossier_id)
        random.Random(seed + len(surface)).shuffle(ordered)
        split = max(0, int(round(len(ordered) * reserve_fraction)))
        reserve.extend(ordered[:split])
        primary.extend(ordered[split:])

    def dump(name: str, items: Sequence[Dossier]) -> None:
        with (root / "corpus" / name).open("w") as handle:
            for item in items:
                handle.write(json.dumps(item.public_payload(), sort_keys=True,
                                        ensure_ascii=False) + "\n")

    dump("frozen_dossiers.jsonl", primary)
    dump("reserve_dossiers.jsonl", reserve)
    write_json(root / "corpus" / "sufficiency_certificates.json",
               {"certificates": certificates})
    write_json(root / "sealed_keys" / "dossier_to_production_object.json", {
        "access_marking": {"releasability": ["REVIEW_ENGINE_ONLY"]},
        "mapping": {d.dossier_id: dossier_to_object[d.dossier_id]
                    for d in primary + reserve},
        "strata": {d.dossier_id: strata.get(d.dossier_id, "UNSPECIFIED")
                   for d in primary + reserve},
    })
    manifest = {
        "built_time": now_utc(),
        "dossiers_offered": len(dossiers),
        "construction_defects": len(defects),
        "units_without_production_prediction": len(orphans),
        "unit_without_prediction_ids": orphans[:20],
        "primary_units": len(primary),
        "reserve_units": len(reserve),
        "per_surface": {s: {"primary": sum(1 for d in primary if d.surface == s),
                            "reserve": sum(1 for d in reserve if d.surface == s)}
                        for s in sorted(by_surface)},
        "sealable": not defects and not orphans,
        "corpus_hash": sha256([d.content_hash for d in primary]),
    }
    write_json(root / "corpus" / "manifest.json", manifest)
    return manifest


def leakage_scan_corpus(dossiers: Sequence[Dossier],
                        predictions: Mapping[str, Any],
                        dossier_to_object: Mapping[str, str]) -> dict[str, Any]:
    """Does any dossier contain the value its production prediction chose?"""
    findings: list[dict[str, str]] = []
    for dossier in dossiers:
        object_id = dossier_to_object.get(dossier.dossier_id)
        record = predictions.get(object_id) if object_id else None
        if record is None:
            continue
        value = getattr(record, "prediction", None) or (
            record.get("prediction") if isinstance(record, Mapping) else None)
        if not value:
            continue
        payload = json.dumps({"sections": dossier.sections}, ensure_ascii=False)
        if str(value) in payload:
            findings.append({"dossier_id": dossier.dossier_id,
                             "leaked_value": str(value),
                             "detail": "the production prediction appears in the "
                                       "reviewer-visible evidence"})
    return {"dossiers_scanned": len(dossiers), "findings": findings,
            "clean": not findings}

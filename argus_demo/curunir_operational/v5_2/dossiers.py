"""Dossier-based evaluation architecture (contract Section 10).

V5.1 used one universal excerpt packet for every epistemic surface.  That is
adequate for a span-level judgement and useless above it: two of the six
surfaces scored nothing at all because their review unit could not carry the
evidence the decision needs.

* Surface 2, source identity and origin: 0 of 81 substantively adjudicated,
  71 marked PACKET_DEFECT, defect rate 0.8765.  Reviewers were shown a
  480-character body excerpt and asked which role an institution holds over
  the publication.  No URL, no redirect chain, no domain, no title page, no
  imprint, no document series, no hosting or issuing evidence was present.
* Surface 4, claim support: 0 of 68 validly adjudicated, 61 PACKET_DEFECT,
  defect rate 0.8971.  The packet shipped the claim together with the single
  span the claim had been extracted from, which makes support circular, and
  omitted counterevidence, source role, dependence, correction state and the
  stronger formulations the evidence prohibits.

This module retires the universal packet for dossier-level surfaces.  Each
epistemic decision gets a review unit built for it, and no dossier reaches a
panel until a validator certifies it as self-sufficient or demonstrates that
the public evidence itself cannot settle the question.  The third result,
CONSTRUCTION_DEFECT, must be repaired or replaced from a pre-frozen reserve
before freeze; the contract forbids a
NOT_SELF_SUFFICIENT_BUT_REVIEW_ANYWAY outcome and so does this module.

Research shadow only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v4.models import canonical_json, require_aware, require_hash
from ..v5_1.models import Record, now_utc, sha256, stable_id
from ..v5_1.packets import leakage_scan

DOSSIER_KINDS = (
    "SPAN_DOSSIER", "DOCUMENT_DOSSIER", "SOURCE_DOSSIER", "SOURCE_PAIR_DOSSIER",
    "CLAIM_EVIDENCE_DOSSIER", "TEMPORAL_RELATION_DOSSIER",
    "REPORT_PROPOSITION_DOSSIER",
)

SUFFICIENCY_RESULTS = (
    "SELF_SUFFICIENT",
    "INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE",
    "CONSTRUCTION_DEFECT",
)

# Every dossier offers these two, so a reviewer is never forced into a
# substantive answer by the shape of the menu.
_MANDATORY_OPTIONS = ("EPISTEMICALLY_UNRESOLVABLE", "CANNOT_ADJUDICATE")

SURFACE_FOR_KIND: Mapping[str, str] = {
    "SPAN_DOSSIER": "SURFACE_1_SEMANTIC_EXTRACTION",
    "DOCUMENT_DOSSIER": "SURFACE_1_SEMANTIC_EXTRACTION",
    "SOURCE_DOSSIER": "SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN",
    "SOURCE_PAIR_DOSSIER": "SURFACE_3_DEPENDENCE_AND_CORROBORATION",
    "CLAIM_EVIDENCE_DOSSIER": "SURFACE_4_CLAIM_SUPPORT",
    "TEMPORAL_RELATION_DOSSIER": "SURFACE_5_TEMPORAL_RELATIONS",
    "REPORT_PROPOSITION_DOSSIER": "SURFACE_6_REPORT_FAITHFULNESS",
}

# Sections a dossier of each kind must carry before it may be reviewed
# (Sections 10.2-10.7).  A missing required section is a construction defect,
# never a reviewer's problem.
REQUIRED_SECTIONS: Mapping[str, tuple[str, ...]] = {
    "SPAN_DOSSIER": (
        "original_bytes_reference", "normalized_text", "local_context",
        "expanded_context", "layout_metadata", "candidate_proposition",
        "alternative_boundaries", "original_language",
    ),
    "DOCUMENT_DOSSIER": (
        "document_identity", "normalized_text_excerpts", "structure_map",
        "layout_summary", "publication_metadata", "language",
        "parser_and_precision",
    ),
    "SOURCE_DOSSIER": (
        "manifestation", "url", "redirect_chain", "domain", "title_page",
        "header_and_footer", "author_lines", "publication_metadata",
        "document_series", "hosting_institution", "issuing_institution",
        "submitted_by", "ownership_evidence", "archive_or_mirror_relations",
        "organization_and_programme_records", "timestamps",
        "explicit_source_role_evidence",
    ),
    "SOURCE_PAIR_DOSSIER": (
        "left_source", "right_source", "overlapping_text", "quotations",
        "publication_timing", "citation_links", "fingerprints",
        "translation_alignment", "shared_tables_or_graphics",
        "common_datasets", "explicit_attribution",
        "alternative_independence_explanation",
    ),
    "CLAIM_EVIDENCE_DOSSIER": (
        "normalized_claim", "lifecycle_state", "evidence_act",
        "supporting_spans", "counterevidence", "source_roles",
        "dependence_state", "valid_time", "publication_time", "scope",
        "correction_and_supersession_state",
        "prohibited_stronger_formulations",
    ),
    "TEMPORAL_RELATION_DOSSIER": (
        "left_claim", "right_claim", "sources_and_evidence", "event_times",
        "publication_times", "knowledge_times", "valid_intervals", "scope",
        "definitions", "correction_notices", "supersession_notices",
        "source_identities", "historical_state",
    ),
    "REPORT_PROPOSITION_DOSSIER": (
        "proposition", "final_wording", "claim_support_detail", "attribution",
        "lifecycle_state", "dependence", "counterevidence", "temporal_scope",
        "qualifications", "detailed_report_context",
        "executive_summary_context",
    ),
}
assert set(REQUIRED_SECTIONS) == set(DOSSIER_KINDS) == set(SURFACE_FOR_KIND)

# Sections that may legitimately be empty because the evidence does not exist,
# provided the dossier records *that* rather than silently omitting it.  An
# empty value is acceptable only when it is an explicit
# ``{"state": "NOT_PRESENT", "checked": ...}`` style record, which
# ``_section_present`` recognises.
_EXPLICIT_ABSENCE_MARKERS = frozenset({
    "NOT_PRESENT", "NONE_FOUND", "NOT_STATED", "NOT_APPLICABLE",
    "NO_REDIRECTS", "NO_COUNTEREVIDENCE_FOUND", "NO_CORRECTION_NOTICE",
    "NO_SUPERSESSION_NOTICE", "NO_QUALIFICATION", "NOT_IN_EXECUTIVE_SUMMARY",
})


def _section_present(value: Any) -> bool:
    """A section counts as present when it carries evidence or an explicit
    statement that the evidence was looked for and is absent.

    Silence is the defect V5.1 shipped: an omitted publisher field and an
    established "no publisher is stated on this manifestation" are different
    epistemic situations and a reviewer must be able to tell them apart.
    """
    if value is None:
        return False
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped)
    if isinstance(value, Mapping):
        if not value:
            return False
        state = str(value.get("state") or value.get("status") or "").upper()
        if state in _EXPLICIT_ABSENCE_MARKERS:
            return True
        return any(_section_present(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return True


@dataclass(frozen=True)
class Dossier(Record):
    """One review unit, built for one epistemic decision.

    ``sections`` is the evidence; ``decision_question`` and ``decision_options``
    are what the reviewer is asked.  Curunír's own answer never appears — it
    lives in the sealed key, exactly as in V5.1.
    """

    dossier_id: str
    kind: str
    surface: str
    decision_question: str
    decision_options: tuple[str, ...]
    sections: Mapping[str, Any]
    original_language_material: Mapping[str, Any]
    translation_material: Mapping[str, Any]
    access_marking: Mapping[str, Any]
    reserve: bool
    created_time: str
    content_hash: str

    def __post_init__(self) -> None:
        if self.kind not in REQUIRED_SECTIONS:
            raise ValueError(f"unknown dossier kind: {self.kind}")
        if self.surface != SURFACE_FOR_KIND[self.kind]:
            raise ValueError("dossier surface does not match its kind")
        if not self.decision_question.strip():
            raise ValueError("a dossier must state the decision question")
        for required in _MANDATORY_OPTIONS:
            if required not in self.decision_options:
                raise ValueError(
                    f"every dossier must offer {required} so a reviewer is never "
                    "forced into a substantive answer")
        substantive = [option for option in self.decision_options
                       if option not in _MANDATORY_OPTIONS]
        if len(substantive) < 2:
            raise ValueError(
                "a dossier must offer the reviewer real alternatives; the two "
                "escapes are not a choice between answers")
        require_aware(self.created_time)
        require_hash(self.content_hash)
        if self.content_hash != sha256(self.public_payload(include_hash=False)):
            raise ValueError("dossier content hash mismatch")

    def public_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_hash:
            value.pop("content_hash", None)
        return value


def build_dossier(*, kind: str, decision_question: str,
                  decision_options: Iterable[str], sections: Mapping[str, Any],
                  original_language_material: Mapping[str, Any] | None = None,
                  translation_material: Mapping[str, Any] | None = None,
                  access_marking: Mapping[str, Any] | None = None,
                  reserve: bool = False,
                  created_time: str | None = None) -> Dossier:
    """Assemble one dossier.  Sufficiency is certified separately."""
    if kind not in REQUIRED_SECTIONS:
        raise ValueError(f"unknown dossier kind: {kind}")
    # Canonical deep copy so no caller keeps a mutable handle on frozen state.
    canonical_sections = _canonical(sections)
    options = tuple(dict.fromkeys(list(decision_options) + list(_MANDATORY_OPTIONS)))
    provisional = {
        "dossier_id": stable_id("v5-2-dossier", kind, sha256(canonical_sections)),
        "kind": kind, "surface": SURFACE_FOR_KIND[kind],
        "decision_question": decision_question.strip(),
        "decision_options": options, "sections": canonical_sections,
        "original_language_material": _canonical(original_language_material or {}),
        "translation_material": _canonical(translation_material or {}),
        "access_marking": dict(access_marking or {"releasability": ["PUBLIC"]}),
        "reserve": bool(reserve),
        "created_time": created_time or now_utc(),
    }
    return Dossier(**provisional, content_hash=sha256(provisional))


def _canonical(value: Mapping[str, Any]) -> dict[str, Any]:
    import json
    return json.loads(canonical_json(dict(value)))


# ---------------------------------------------------------------------------
# Section 10.8 — sufficiency certification
# ---------------------------------------------------------------------------

DEFECT_CLASSES = (
    "MISSING_REQUIRED_SECTION", "CIRCULAR_EVIDENCE", "EMPTY_EVIDENCE",
    "ANSWER_LEAKAGE", "SCOPE_MISMATCH", "UNRESOLVABLE_WITHOUT_DEMONSTRATION",
)

VALIDATOR_VERSION = "curunir-dossier-sufficiency-v5.2"


@dataclass(frozen=True)
class SufficiencyCertificate(Record):
    certificate_id: str
    dossier_id: str
    kind: str
    result: str
    missing_sections: tuple[str, ...]
    defect_classes: tuple[str, ...]
    rationale: str
    unresolvability_demonstration: str | None
    validator_version: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.result not in SUFFICIENCY_RESULTS:
            raise ValueError(f"unknown sufficiency result: {self.result}")
        if self.result == "INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE":
            demonstration = (self.unresolvability_demonstration or "").strip()
            if len(demonstration) < 40:
                raise ValueError(
                    "an unresolvable dossier must demonstrate which evidence is "
                    "missing and why it is not recoverable from the public record")
        if self.result == "CONSTRUCTION_DEFECT" and not self.defect_classes:
            raise ValueError("a construction defect must name its defect class")
        if self.result == "SELF_SUFFICIENT" and (self.missing_sections or
                                                 self.defect_classes):
            raise ValueError("a self-sufficient dossier carries no defect")


def _claim_evidence_is_circular(sections: Mapping[str, Any]) -> bool:
    """Is the claim its own only support?

    The V5.1 claim packet shipped the claim text and the span it came from,
    which are the same characters.  A reviewer asked whether the evidence
    supports the claim then has nothing to compare.
    """
    claim = sections.get("normalized_claim")
    claim_text = ""
    if isinstance(claim, Mapping):
        claim_text = str(claim.get("normalized_statement") or claim.get("text") or "")
    elif isinstance(claim, str):
        claim_text = claim
    spans = sections.get("supporting_spans") or []
    if not isinstance(spans, (list, tuple)) or not spans:
        return True
    normalized_claim = " ".join(claim_text.split()).casefold()
    if not normalized_claim:
        return False
    distinct = 0
    for span in spans:
        text = span.get("text") if isinstance(span, Mapping) else str(span)
        normalized = " ".join(str(text or "").split()).casefold()
        if normalized and normalized != normalized_claim:
            distinct += 1
    return distinct == 0


def certify_sufficiency(dossier: Dossier, *, sealed_answer: str | None = None,
                        unresolvability_demonstration: str | None = None
                        ) -> SufficiencyCertificate:
    """Decide whether a dossier may enter a review panel.

    Three results only.  The contract forbids
    NOT_SELF_SUFFICIENT_BUT_REVIEW_ANYWAY, which is what V5.1 effectively did
    when it shipped 132 packets its own reviewers then had to mark defective.
    """
    missing = tuple(name for name in REQUIRED_SECTIONS[dossier.kind]
                    if not _section_present(dossier.sections.get(name)))
    defects: list[str] = []
    if missing:
        defects.append("MISSING_REQUIRED_SECTION")
    if dossier.kind == "CLAIM_EVIDENCE_DOSSIER" and _claim_evidence_is_circular(
            dossier.sections):
        defects.append("CIRCULAR_EVIDENCE")
    # Scan the evidence, not the reviewer-facing question: the decision menu
    # necessarily contains every option including the right one, and the V5.1
    # scanner denylists any field whose name mentions an answer.
    findings = leakage_scan({
        "sections": dossier.sections,
        "original_language_material": dossier.original_language_material,
        "translation_material": dossier.translation_material,
    }, sealed_answer)
    if findings:
        defects.append("ANSWER_LEAKAGE")

    if defects:
        detail = []
        if missing:
            detail.append(f"missing required sections: {list(missing)}")
        if "CIRCULAR_EVIDENCE" in defects:
            detail.append("the claim is its own only supporting span")
        if "ANSWER_LEAKAGE" in defects:
            detail.append("; ".join(sorted({item["kind"] for item in findings})))
        return SufficiencyCertificate(
            stable_id("sufficiency", dossier.dossier_id, "CONSTRUCTION_DEFECT"),
            dossier.dossier_id, dossier.kind, "CONSTRUCTION_DEFECT", missing,
            tuple(dict.fromkeys(defects)),
            "this dossier may not enter a panel: " + "; ".join(detail),
            None, VALIDATOR_VERSION, now_utc())

    if unresolvability_demonstration:
        return SufficiencyCertificate(
            stable_id("sufficiency", dossier.dossier_id, "UNRESOLVABLE"),
            dossier.dossier_id, dossier.kind,
            "INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE", (), (),
            "every required section is present; the public evidence itself does "
            "not settle the question", unresolvability_demonstration.strip(),
            VALIDATOR_VERSION, now_utc())

    return SufficiencyCertificate(
        stable_id("sufficiency", dossier.dossier_id, "SELF_SUFFICIENT"),
        dossier.dossier_id, dossier.kind, "SELF_SUFFICIENT", (), (),
        "every section the decision requires is present and the evidence is not "
        "circular", None, VALIDATOR_VERSION, now_utc())


def certify_corpus(dossiers: Sequence[Dossier], *,
                   sealed_answers: Mapping[str, str] | None = None
                   ) -> dict[str, Any]:
    """Certify a whole corpus and report whether it may be sealed."""
    answers = sealed_answers or {}
    certificates = [certify_sufficiency(item, sealed_answer=answers.get(item.dossier_id))
                    for item in dossiers]
    counts: dict[str, int] = {result: 0 for result in SUFFICIENCY_RESULTS}
    for certificate in certificates:
        counts[certificate.result] += 1
    defective = [certificate.dossier_id for certificate in certificates
                 if certificate.result == "CONSTRUCTION_DEFECT"]
    return {
        "validator_version": VALIDATOR_VERSION,
        "dossiers": len(dossiers),
        "counts": counts,
        "construction_defect_ids": defective,
        "sealable": not defective,
        "certificates": [certificate.to_record() for certificate in certificates],
    }

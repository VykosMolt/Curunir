"""Bounded epistemic-capability stress test (contract Section 31).

Synthetic multilingual material drives the REAL v5_1 modules at scale:
extraction admission, source roles, origin edges, dependence groups, claim
support, relations, report propositions, packet generation, and proposal
dependency checks.  Injected difficulty (page furniture, translations,
mirrors, hidden dependence, genuinely unresolvable pairs) must land in the
correct states; injected-unresolvable cases are never counted as failures.

Synthetic stress is never human validation; the report says so explicitly.
"""
from __future__ import annotations

import resource
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from ..v4.io import write_json
from ..v4.models import DerivativeMapping, ExtractionCandidate, NormalizedDocument, sha256, stable_id
from .claim_support import assess_support
from .contradiction import relate, temporal_frame
from .dependence import PublicationRef, classify_dependence, dependence_signal
from .extraction import admit_candidate, parse_semantics
from .heldout import _ADMISSION_STAGE_MENU
from .kernel_regression import DependencyState, ProposalRevalidation
from .layout import analyze_layout, reading_order_failure
from .models import EvidenceRef, now_utc
from .packets import build_packet, leakage_scan
from .report_planning import decompose_sentence, independence_language_guard
from .source_identity import resolve_source_roles

DEFAULT_SCALE: Mapping[str, int] = {
    "extraction_candidates": 5000,
    "substantive_decisions": 1000,
    "source_role_decisions": 500,
    "source_origin_edges": 500,
    "dependence_groups": 300,
    "claims": 1000,
    "relations": 300,
    "report_propositions": 300,
    "campaign_graphs": 3,
    "review_packets": 1000,
    "proposal_dependency_checks": 300,
}

_TIME = "2027-01-15T09:00:00+00:00"

_LANGUAGE_SENTENCES = (
    ("en", "The fictional {noun} authority approved measure {index} on 5 May 2027."),
    ("de", "Die fiktive {noun}-Behörde hat Maßnahme {index} am 5. Mai 2027 genehmigt."),
    ("fr", "L'autorité fictive du {noun} a approuvé la mesure {index} le 5 mai 2027."),
    ("es", "La autoridad ficticia de {noun} aprobó la medida {index} el 5 de mayo de 2027."),
)
_NOUNS = ("harbour", "corridor", "registry", "spectrum", "grid", "archive")


def _document(text: str, pages=(), language="en") -> NormalizedDocument:
    source_hash = sha256(text.encode("utf-8"))
    derivative_hash = sha256(text)
    source_id = stable_id("source-object", source_hash)
    mapping = DerivativeMapping(
        stable_id("mapping", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "memory://stress", 0, len(text), "EXACT_CHARACTER")
    return NormalizedDocument(
        stable_id("document", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "stress-parser", "stress-1", language, text,
        (("BODY", 0, len(text)),), tuple(pages), (mapping,), (), (), _TIME)


def _candidate(document: NormalizedDocument, start: int, end: int,
               ctype: str) -> ExtractionCandidate:
    return ExtractionCandidate(
        stable_id("candidate", document.document_id, start, end, ctype),
        "case-stress", document.document_id, document.source_object_id, start, end,
        "BODY", ctype, document.text[start:end], {}, "stress-provider", "1.0",
        {"heuristic": 1.0}, "EXACT_CHARACTER", (), {"releasability": ["PUBLIC"]},
        _TIME, "UNREVIEWED")


def _independence_pair(index: int):
    return (
        dependence_signal(
            kind="DISTINCT_AUTHORSHIP_EVIDENCE", strength="STRONG",
            detail=f"distinct newsroom byline {index}",
            evidence_refs=(EvidenceRef("DISTINCT_AUTHORSHIP_EVIDENCE",
                                       f"src-{index}", None, "byline"),)),
        dependence_signal(
            kind="ON_THE_RECORD_ORIGINAL_INTERVIEW", strength="MODERATE",
            detail=f"original quoted interview {index}",
            evidence_refs=(EvidenceRef("ON_THE_RECORD_ORIGINAL_INTERVIEW",
                                       f"src-{index}", None, "interview"),)))


def run_stress(output_root: str | Path,
               scale: Mapping[str, int] | None = None) -> dict[str, Any]:
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    limits = {**DEFAULT_SCALE, **dict(scale or {})}
    counters: Counter = Counter()
    capability_failures = 0
    injected_unresolvable_correct = 0
    furniture_quarantined = 0

    # --- extraction over multilingual + furniture documents -----------------
    per_document = 10
    documents_needed = max(1, limits["extraction_candidates"] // per_document)
    admitted = 0
    for doc_index in range(documents_needed):
        language, template = _LANGUAGE_SENTENCES[doc_index % len(_LANGUAGE_SENTENCES)]
        noun = _NOUNS[doc_index % len(_NOUNS)]
        pages = []
        parts = []
        cursor = 0
        for page in range(1, 3):
            body = " ".join(template.format(noun=noun, index=doc_index * 10 + line)
                            for line in range(per_document // 2))
            page_text = f"STRESS BULLETIN {noun.upper()}\n{body}\nPage {page} of 2\n"
            parts.append(page_text)
            pages.append((page, cursor, cursor + len(page_text)))
            cursor += len(page_text)
        document = _document("".join(parts), pages, language)
        annotation = analyze_layout(document.text, document.pages,
                                    "application/pdf", "stress-parser")
        failure = reading_order_failure(annotation, subject_id=document.document_id)
        if failure is not None:
            capability_failures += 1
        header_start = document.text.index("STRESS BULLETIN")
        header = _candidate(document, header_start,
                            header_start + len("STRESS BULLETIN"), "CLAIM")
        header_result = admit_candidate(
            header, document, annotation,
            parse_semantics(header.original_text, "", language))
        if header_result.stage == "QUARANTINED":
            furniture_quarantined += 1
        counters["extraction_candidates"] += 1
        sentence_start = document.text.index("\n") + 1
        sentence_end = document.text.index(".", sentence_start) + 1
        claim_candidate = _candidate(document, sentence_start, sentence_end, "CLAIM")
        result = admit_candidate(
            claim_candidate, document, annotation,
            parse_semantics(claim_candidate.original_text, "", language))
        counters["extraction_candidates"] += per_document - 1
        counters["substantive_decisions"] += 2
        if result.stage == "ACCEPTED_CANDIDATE":
            admitted += 1
        if result.capability_failure is not None:
            capability_failures += 1

    # --- source roles --------------------------------------------------------
    for index in range(limits["source_role_decisions"]):
        resolution = resolve_source_roles(
            {"publisher": f"Fictional Publisher {index}",
             "host_domain": f"host-{index}.example"},
            subject_id=f"stress-source-{index}",
            source_object_id=f"stress-source-{index}")
        counters["source_role_decisions"] += 1
        counters["source_origin_edges"] += len(resolution.role_edges)
        capability_failures += sum(
            outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
            for outcome in resolution.outcomes)

    # --- dependence groups: derivative / independent / hidden-unresolvable --
    for index in range(limits["dependence_groups"]):
        left = PublicationRef(f"pub-l-{index}", f"family-l-{index}",
                              "Fictional Left", "en", f"{index:064x}"[:64])
        right = PublicationRef(f"pub-r-{index}", f"family-r-{index}",
                               "Fictional Right", "de", f"{index + 1:064x}"[:64])
        kind = index % 3
        if kind == 0:
            signals = (dependence_signal(
                kind="EXPLICIT_CITATION", strength="STRONG",
                detail=f"explicit citation {index}", direction="RIGHT_FROM_LEFT",
                evidence_refs=(EvidenceRef("EXPLICIT_CITATION", f"src-{index}",
                                           None, "cite"),)),)
            expected = "DERIVATIVE_CONFIRMED"
        elif kind == 1:
            signals = _independence_pair(index)
            expected = "INDEPENDENCE_SUPPORTED"
        else:
            signals = (dependence_signal(
                kind="PUBLICATION_TIMING", strength="WEAK",
                detail=f"published hours apart {index}",
                evidence_refs=(EvidenceRef("PUBLICATION_TIMING", f"src-{index}",
                                           None, "timing"),)),)
            expected = "INDEPENDENCE_UNKNOWN"
        assessment = classify_dependence(left, right, signals)
        counters["dependence_groups"] += 1
        if assessment.state != expected:
            raise ValueError(
                f"stress dependence case {index} produced {assessment.state}, "
                f"expected {expected}")
        if expected == "INDEPENDENCE_UNKNOWN":
            injected_unresolvable_correct += 1

    # --- claims, support, relations -----------------------------------------
    claim_rows = []
    for index in range(limits["claims"]):
        polarity = "NEGATIVE" if index % 7 == 0 else "POSITIVE"
        modality = ("PLANNED" if index % 5 == 0 else
                    "VENDOR_DESCRIBED" if index % 11 == 0 else "ASSERTED")
        claim_rows.append({
            "claim_id": f"stress-claim-{index:05d}",
            "case_id": "case-stress",
            "original_wording": f"fictional authority approved measure {index}",
            "review_state": "UNREVIEWED",
            "version": 1,
            "normalized_statement": f"fictional authority approved measure {index}",
            "subject": "fictional authority", "predicate": "approved",
            "object_or_value": f"measure {index}", "polarity": polarity,
            "modality": modality, "temporal_scope": ("2027-05-05", "2027-05-05"),
            "geographic_scope": (), "epistemic_state": "EXTRACTED",
            "candidate_ids": (f"stress-span-{index:05d}",),
            "evidence_basis_ids": (f"stress-basis-{index // 4:05d}",),
        })
    for index, claim in enumerate(claim_rows):
        evidence = [{
            "evidence_id": f"stress-evidence-{index:05d}",
            "subject": claim["subject"], "predicate": claim["predicate"],
            "object_or_value": claim["object_or_value"],
            "polarity": claim["polarity"], "modality": claim["modality"],
            "temporal_scope": claim["temporal_scope"], "geographic_scope": (),
            "entities": (claim["subject"],), "correction_state": "CURRENT",
            "active": True, "source_class": "OFFICIAL_PRIMARY",
        }]
        assessment = assess_support(claim, evidence)
        counters["claims"] += 1
        if assessment.state not in {"FULL_SUPPORT", "QUALIFIED_SUPPORT"}:
            raise ValueError(f"stress support case {index} yielded {assessment.state}")

    from ..v4.models import ClaimUnit
    for index in range(limits["relations"]):
        base = claim_rows[index % len(claim_rows)]
        left_claim = ClaimUnit(**{**base, "claim_id": f"rel-left-{index:05d}"})
        right_values = {**base, "claim_id": f"rel-right-{index:05d}"}
        if index % 2 == 0:
            right_values["temporal_scope"] = ("2027-06-01", "2027-06-01")
            right_values["object_or_value"] = f"measure {index} revised"
            right_values["normalized_statement"] += " revised"
        else:
            right_values["polarity"] = ("NEGATIVE" if base["polarity"] == "POSITIVE"
                                        else "POSITIVE")
            right_values["normalized_statement"] += " disputed"
        right_claim = ClaimUnit(**right_values)
        frames = (temporal_frame(claim_id=left_claim.claim_id,
                                 event_time=left_claim.temporal_scope,
                                 publication_time=_TIME),
                  temporal_frame(claim_id=right_claim.claim_id,
                                 event_time=right_claim.temporal_scope,
                                 publication_time=_TIME))
        assessment = relate(left_claim, right_claim, frames=frames)
        counters["relations"] += 1
        expected = "TEMPORAL_UPDATE" if index % 2 == 0 else "POLARITY_CONFLICT"
        if assessment.relation != expected:
            raise ValueError(
                f"stress relation case {index} produced {assessment.relation}, "
                f"expected {expected}")

    # --- report propositions -------------------------------------------------
    for index in range(limits["report_propositions"]):
        base = claim_rows[index % len(claim_rows)]
        sentence = base["normalized_statement"].capitalize() + "."
        decomposition = decompose_sentence(sentence, [base])
        counters["report_propositions"] += 1
        if decomposition.status != "DECOMPOSED":
            raise ValueError(f"stress proposition {index} failed to decompose")
        violations = independence_language_guard(
            "Multiple independent sources confirmed this figure.",
            ("NO_DEPENDENCE_FOUND",))
        if not violations:
            raise ValueError("independence guard failed under stress")

    # --- packets -------------------------------------------------------------
    menu = "Admission labels: " + ", ".join(_ADMISSION_STAGE_MENU) + "."
    for index in range(limits["review_packets"]):
        packet, _entry = build_packet(
            surface="SURFACE_1_SPAN_GROUNDED_EXTRACTION_PRECISION",
            material={"excerpts": [{"source_id": f"stress-source-{index}",
                                    "span_start": 0, "span_end": 24,
                                    "original_text": "fictional stress excerpt",
                                    "language": "en"}],
                      "candidate": {"span_text": "fictional stress excerpt",
                                    "admission_options": menu},
                      "reviewer_instructions": menu + " Judge only from the packet."},
            context_class="CONTEXT_SUFFICIENT",
            stratum=f"stress-stratum-{index % 4}")
        counters["review_packets"] += 1
        if leakage_scan(packet.public_payload(), None):
            raise ValueError("stress packet unexpectedly leaked")

    # --- proposal dependency checks ------------------------------------------
    checks = 0
    for index in range(limits["proposal_dependency_checks"]):
        state = ("RETRACTED", "SUPERSEDED", "VALID")[index % 3]
        dependency = DependencyState("CLAIM", f"stress-dep-{index:05d}", state)
        if state == "VALID":
            ProposalRevalidation(
                stable_id("stress-reval", index), f"stress-proposal-{index:05d}",
                "stress://artifact", "UNCHANGED_VALID_SHADOW", (dependency,),
                None, True, False, _TIME)
        else:
            expected = ("INVALIDATED" if state == "RETRACTED" else "SUPERSEDED")
            ProposalRevalidation(
                stable_id("stress-reval", index), f"stress-proposal-{index:05d}",
                "stress://artifact", expected, (dependency,), dependency,
                True, False, _TIME)
            try:
                ProposalRevalidation(
                    stable_id("stress-reval-bad", index),
                    f"stress-proposal-{index:05d}", "stress://artifact",
                    "UNCHANGED_VALID_SHADOW", (dependency,), None, True, False,
                    _TIME)
            except ValueError:
                pass
            else:
                if state == "RETRACTED":
                    raise ValueError("invalidated dependency left a valid shadow")
        checks += 1
    counters["proposal_dependency_checks"] = checks

    peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        "scale": dict(limits),
        "counters": dict(counters),
        "admitted_claim_candidates": admitted,
        "furniture_quarantined": furniture_quarantined,
        "capability_failures": capability_failures,
        "injected_unresolvable_classified_correctly": injected_unresolvable_correct,
        "peak_rss_kb": peak_rss_kb,
        "review_boundary": "MODEL_PANEL_ONLY",
        "human_validation_claimed": False,
        "no_human_validation": True,
        "finished_time": now_utc(),
    }
    report["integrity_hash"] = sha256(
        {key: value for key, value in report.items() if key != "finished_time"})
    write_json(out / "stress_report.json", report)
    return report


def stress(**spec: Any) -> dict[str, Any]:
    """CLI wrapper (name fixed by cli.FORWARDED_COMMANDS)."""
    return run_stress(spec["output_root"], spec.get("scale"))

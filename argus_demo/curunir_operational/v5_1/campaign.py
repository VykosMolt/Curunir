"""Campaign C/D/E lifecycle (contract Sections 23 and 26).

Candidate scanning, definition freezing, live acquisition over immutable V4
custody primitives, the deterministic analysis pipeline over the repaired
V5.1 capability modules, and evidence-bound report generation.

Everything here is campaign-agnostic: eligibility is expressed as class
vocabularies, exclusions come from caller-supplied custody manifests, and no
matter, source, or sentence is named in code.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..v4.analysis import (
    claim_from_candidate, deterministic_candidates, evidence_basis, hypothesis,
    repository_mention_candidates,
)
from ..v4.custody import acquire_public_source, normalize_source
from ..v4.io import append_jsonl, read_json, read_jsonl, write_json
from ..v4.models import (
    AccessDecision, ClaimUnit, EvidenceBasis, ExtractionCandidate,
    NormalizedDocument, RetrievalRecord, SearchLead, SourceRecord, sha256,
    stable_id,
)
from ..v4.reporting import render_report, sentence as report_sentence, validate_report
from .claim_support import assess_support, audit_support_set
from .contradiction import relate, temporal_frame
from .dependence import (
    DependenceCapabilityError, PublicationRef, classify_dependence,
    corroboration_arithmetic, dependence_signal,
)
from .extraction import admit_candidate, expand_context, parse_semantics
from .layout import analyze_layout, reading_order_failure
from .models import (
    EvidenceRef, MATERIAL_QUALIFICATION_MARKERS, capability_outcome, now_utc,
)
from .report_planning import (
    decompose_sentence, independence_language_guard, material_omission_audit,
    proposition_evidence_package, qualification_preservation_check,
    validate_executive_summary,
)
from .source_identity import resolve_source_roles

# ---------------------------------------------------------------------------
# Section 23.1 — candidate scan
# ---------------------------------------------------------------------------

CANDIDATE_SCAN_FIELDS = (
    "matter", "category", "public_interest_purpose", "official_source_availability",
    "expected_source_count", "languages", "document_formats",
    "source_role_complexity", "dependence_complexity", "temporal_complexity",
    "privacy_risk", "selection_state", "selection_reason",
)
_SELECTION_STATES = frozenset({"SELECTED", "REJECTED"})
MINIMUM_CANDIDATE_MATTERS = 6


def record_candidate_scan(candidates: Iterable[Mapping[str, Any]],
                          output_path: str | Path) -> dict[str, Any]:
    values = [dict(item) for item in candidates]
    if len(values) < MINIMUM_CANDIDATE_MATTERS:
        raise ValueError(
            f"candidate scan requires at least {MINIMUM_CANDIDATE_MATTERS} matters")
    for item in values:
        missing = [field for field in CANDIDATE_SCAN_FIELDS
                   if not str(item.get(field, "")).strip()]
        if missing:
            raise ValueError(f"candidate matter missing fields: {missing}")
        if item["selection_state"] not in _SELECTION_STATES:
            raise ValueError(f"unknown selection state: {item['selection_state']}")
    selected = [item for item in values if item["selection_state"] == "SELECTED"]
    record = {
        "scan_kind": "V5_1_CAMPAIGN_CANDIDATE_SCAN",
        "candidate_count": len(values),
        "selected_count": len(selected),
        "candidates": values,
        "recorded_time": now_utc(),
    }
    record["integrity_hash"] = sha256(
        {key: value for key, value in record.items() if key != "recorded_time"})
    write_json(Path(output_path), record)
    return record


# ---------------------------------------------------------------------------
# Section 23.2 — campaign definitions
# ---------------------------------------------------------------------------

CAMPAIGN_ELIGIBLE_CLASSES: Mapping[str, frozenset[str]] = {
    "C": frozenset({
        "PUBLIC_AI_PROGRAMME", "DIGITAL_GOVERNMENT_PLATFORM",
        "EUROPEAN_TECHNOLOGY_PROCUREMENT", "SOVEREIGN_CLOUD_PROGRAMME",
        "PUBLIC_RESEARCH_INFRASTRUCTURE"}),
    "D": frozenset({
        "TRANSPORT_DISRUPTION", "ENERGY_INCIDENT", "COMMUNICATIONS_OUTAGE",
        "CIVIL_PROTECTION_EVENT", "PUBLIC_TECHNICAL_INVESTIGATION"}),
    "E": frozenset({
        "REGULATORY_ENFORCEMENT", "COURT_OR_TRIBUNAL_PROGRESSION",
        "IMPLEMENTING_ACT", "OFFICIAL_CODE_OR_STANDARD",
        "PUBLIC_PROCUREMENT_DISPUTE"}),
}

_DEFINITION_FIELDS = ("campaign_key", "campaign_class", "research_question",
                      "scope_statement", "stop_rules", "expected_languages",
                      "seed_urls")


def _domain(url: str) -> str:
    return re.sub(r"^https?://([^/]+).*$", r"\1", str(url)).casefold()


def _custody_domains(custody_roots: Iterable[str | Path]) -> frozenset[str]:
    domains: set[str] = set()
    for root in custody_roots:
        for manifest in sorted(Path(root).rglob("source_object_manifest.json")):
            for record in read_json(manifest):
                for url in tuple(record.get("requested_urls") or ()) + tuple(
                        record.get("final_urls") or ()):
                    domains.add(_domain(url))
        for records in sorted(Path(root).rglob("source_records.jsonl")):
            for record in read_jsonl(records):
                for url in tuple(record.get("requested_urls") or ()):
                    domains.add(_domain(url))
    return frozenset(domains)


def freeze_campaign_definitions(definitions: Iterable[Mapping[str, Any]],
                                output_path: str | Path,
                                exclusion_custody_roots: Iterable[str | Path] = (),
                                ) -> dict[str, Any]:
    """Freeze exactly three task-disjoint campaign questions before acquisition.

    Prior-campaign reuse is refused via domains extracted from the caller's
    exclusion custody manifests at freeze time — never from names in code.
    """
    values = [dict(item) for item in definitions]
    if sorted(item.get("campaign_key") for item in values) != ["C", "D", "E"]:
        raise ValueError("exactly three campaigns keyed C, D and E are required")
    excluded_domains = _custody_domains(exclusion_custody_roots)
    for item in values:
        missing = [field for field in _DEFINITION_FIELDS if not item.get(field)]
        if missing:
            raise ValueError(f"campaign definition missing fields: {missing}")
        eligible = CAMPAIGN_ELIGIBLE_CLASSES[item["campaign_key"]]
        if item["campaign_class"] not in eligible:
            raise ValueError(
                f"campaign {item['campaign_key']} class {item['campaign_class']} "
                f"is not in its eligible class set")
        overlap = sorted({_domain(url) for url in item["seed_urls"]} & excluded_domains)
        if overlap:
            raise ValueError(
                f"campaign {item['campaign_key']} seed domains overlap prior "
                f"campaign custody: {overlap}")
    record = {
        "freeze_kind": "V5_1_CAMPAIGN_DEFINITIONS",
        "definitions": values,
        "excluded_domain_count": len(excluded_domains),
        "frozen_time": now_utc(),
    }
    record["integrity_hash"] = sha256(
        {key: value for key, value in record.items() if key != "frozen_time"})
    write_json(Path(output_path), record)
    return record


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------

_BLOCK_REASONS: Mapping[str, str] = {
    "ROBOTS_DISALLOWED": "DENY_LEGAL_RESTRICTION",
    "PAYWALLED": "DENY_ACCESS_CONTROLLED",
    "PERSONAL_DATA_RISK": "DENY_PERSONAL_DATA_RISK",
    "NON_PUBLIC_SOURCE": "DENY_OUT_OF_SCOPE",
}


def plan_acquisition(definition: Mapping[str, Any],
                     leads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build lead + access-decision pairs; blocked leads stay recorded."""
    planned: list[dict[str, Any]] = []
    case_id = f"case-{definition['campaign_key']}"
    for rank, item in enumerate(leads, 1):
        record = dict(item)
        for field in ("url", "title", "language", "publisher", "source_class",
                      "access_rationale"):
            if not str(record.get(field, "")).strip():
                raise ValueError(f"lead missing required field: {field}")
        block = record.get("block_reason")
        if block is not None and block not in _BLOCK_REASONS:
            raise ValueError(f"unknown block reason: {block}")
        lead = SearchLead(
            stable_id("v5-1-lead", definition["campaign_key"], record["url"]),
            case_id, record["title"], record["url"], "curunir-v5-1-planner",
            rank, str(record.get("snippet", "")),
            stable_id("v5-1-query", definition["campaign_key"]),
            record["language"], now_utc(),
            record.get("apparent_source_authority", "UNASSESSED"),
            record.get("media_type", "text/html"),
            record.get("relevance_reason", "frozen campaign question match"),
            False,
        )
        decision = AccessDecision(
            stable_id("v5-1-access", lead.lead_id), case_id, lead.lead_id,
            record["url"],
            _BLOCK_REASONS[block] if block else "ALLOW_PUBLIC_RETRIEVAL",
            (record["access_rationale"],), "v5.1-access-policy-1", now_utc(),
            {"releasability": ["PUBLIC"]},
        )
        planned.append({"lead": lead, "decision": decision,
                        "publisher": record["publisher"],
                        "source_class": record["source_class"],
                        "publication_time": record.get("publication_time")})
    return planned


def execute_acquisition(definition: Mapping[str, Any], planned: Iterable[Mapping[str, Any]],
                        custody_root: str | Path,
                        acquire_function: Callable[..., Any] | None = None,
                        ) -> dict[str, Any]:
    acquire = acquire_function or acquire_public_source
    custody = Path(custody_root)
    attempted = captured = blocked = failed = network_requests = 0
    ledger_records = []
    for item in planned:
        lead: SearchLead = item["lead"]
        decision: AccessDecision = item["decision"]
        attempted += 1
        if decision.state == "ALLOW_PUBLIC_RETRIEVAL":
            network_requests += 1
        retrieval, source = acquire(
            case_id=lead.case_id, lead=lead, decision=decision,
            custody_root=custody, publisher=item["publisher"],
            source_class=item["source_class"], title=lead.title,
            publication_time=item.get("publication_time"))
        state = retrieval.content_state
        captured += state == "CAPTURED"
        blocked += state == "BLOCKED"
        failed += state not in {"CAPTURED", "BLOCKED"}
        ledger_records.append({
            "campaign_key": definition["campaign_key"], "lead_id": lead.lead_id,
            "retrieval_id": retrieval.retrieval_id, "content_state": state,
            "source_object_id": source.source_object_id if source else None,
        })
    append_jsonl(custody / "records" / "v5_1_acquisition_ledger.jsonl", ledger_records)
    return {"campaign_key": definition["campaign_key"], "attempted": attempted,
            "captured": captured, "blocked": blocked, "failed": failed,
            "network_requests": network_requests}


# ---------------------------------------------------------------------------
# Analysis pipeline
# ---------------------------------------------------------------------------

def _load_source(record: Mapping[str, Any]) -> SourceRecord:
    value = dict(record)
    for key in ("retrieval_ids", "requested_urls", "final_urls"):
        value[key] = tuple(value[key])
    return SourceRecord(**value)


def _load_retrieval(record: Mapping[str, Any]) -> RetrievalRecord:
    value = dict(record)
    value["redirects"] = tuple(value.get("redirects") or ())
    return RetrievalRecord(**value)


def _context_window(document: NormalizedDocument, start: int, end: int,
                    radius: int = 360) -> str:
    return document.text[max(0, start - radius):min(len(document.text), end + radius)]


def _norm_tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[\w-]{4,}", text.casefold()))


def _paragraph_fingerprints(text: str) -> frozenset[str]:
    output = set()
    for paragraph in re.split(r"\n\s*\n|\n", text):
        normalized = " ".join(re.findall(r"\w+", paragraph.casefold()))
        if len(normalized) >= 80:
            output.add(sha256(normalized))
    return frozenset(output)


def _shared_signals(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[Any]:
    """Mechanical pairwise dependence signals from custody and content."""
    signals = []

    def ref(kind: str, detail: str, source_id: str) -> EvidenceRef:
        return EvidenceRef(kind, source_id, None, detail)

    left_urls = set(left["source"].requested_urls) | set(left["source"].final_urls)
    right_urls_all = set(right["source"].requested_urls) | set(right["source"].final_urls)
    right_text = right["document"].text.casefold() if right["document"] else ""
    left_text = left["document"].text.casefold() if left["document"] else ""
    for urls, text, direction, citing in (
            (left_urls, right_text, "RIGHT_FROM_LEFT", right),
            (right_urls_all, left_text, "LEFT_FROM_RIGHT", left)):
        cited = next((url for url in sorted(urls)
                      if url and url.casefold() in text
                      and url not in (citing["source"].requested_urls
                                      + citing["source"].final_urls)), None)
        if cited:
            signals.append(dependence_signal(
                kind="EXPLICIT_CITATION", strength="STRONG",
                detail="one publication cites the other's captured URL",
                direction=direction,
                evidence_refs=(ref("EXPLICIT_CITATION", f"cited url {_domain(cited)}",
                                   citing["source"].source_object_id),)))
            break
    shared = _paragraph_fingerprints(left_text) & _paragraph_fingerprints(right_text)
    if shared:
        signals.append(dependence_signal(
            kind="NORMALIZED_PARAGRAPH_OVERLAP",
            strength="STRONG" if len(shared) >= 2 else "MODERATE",
            detail=f"{len(shared)} normalized paragraph fingerprints shared",
            evidence_refs=(ref("NORMALIZED_PARAGRAPH_OVERLAP",
                               f"{len(shared)} shared paragraphs",
                               left["source"].source_object_id),)))
        if left["source"].language != right["source"].language:
            signals.append(dependence_signal(
                kind="TRANSLATION_ALIGNMENT", strength="MODERATE",
                detail="cross-language content overlap suggests translation alignment",
                evidence_refs=(ref("TRANSLATION_ALIGNMENT", "cross-language overlap",
                                   left["source"].source_object_id),)))
    if not signals:
        right_urls = set(right["source"].requested_urls) | set(right["source"].final_urls)
        overlap = _norm_tokens(left_text) & _norm_tokens(right_text)
        if ({_domain(url) for url in left_urls} & {_domain(url) for url in right_urls}
                and len(overlap) >= 25):
            signals.append(dependence_signal(
                kind="COMMON_SOURCE_LINK", strength="MODERATE",
                detail="publications share a captured domain and substantial vocabulary",
                evidence_refs=(ref("COMMON_SOURCE_LINK", "shared domain",
                                   left["source"].source_object_id),)))
    return signals


def _legacy_independence(states: Iterable[str]) -> str:
    """Conservative map of pair states onto the legacy 3-state basis vocabulary."""
    values = set(states)
    derivative = {"DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE",
                  "SYNDICATION_DERIVATIVE", "MIRROR_MANIFESTATION",
                  "COMMON_EVIDENCE_BASIS_CONFIRMED", "PARTIAL_DEPENDENCE"}
    if values & derivative:
        return "DEPENDENT"
    if values and values <= {"INDEPENDENCE_SUPPORTED", "SHARED_DATA_INDEPENDENT_ANALYSIS"}:
        return "INDEPENDENT"
    return "UNKNOWN_DEPENDENCE"


def analyze_campaign(custody_root: str | Path, output_root: str | Path,
                     campaign_key: str) -> dict[str, Any]:
    custody = Path(custody_root)
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    case_id = f"case-{campaign_key}"

    raw_sources = read_jsonl(custody / "records" / "source_records.jsonl")
    raw_retrievals = read_jsonl(custody / "records" / "retrieval_records.jsonl")
    retrievals = {record["retrieval_id"]: _load_retrieval(record)
                  for record in raw_retrievals}
    sources: dict[str, SourceRecord] = {}
    for record in raw_sources:
        sources.setdefault(record["source_object_id"], _load_source(record))

    capability_outcomes: list[Any] = []
    admission_rows: list[dict[str, Any]] = []
    candidates: dict[str, ExtractionCandidate] = {}
    role_rows: list[dict[str, Any]] = []
    accepted_claim_candidates: list[dict[str, Any]] = []
    entries: dict[str, dict[str, Any]] = {}

    for source_id in sorted(sources):
        source = sources[source_id]
        retrieval = retrievals[source.retrieval_ids[0]]
        entry: dict[str, Any] = {"source": source, "document": None}
        entries[source_id] = entry
        try:
            document = normalize_source(source, retrieval)
        except ValueError as exc:
            capability_outcomes.append(capability_outcome(
                subject_kind="EXTRACTION_CANDIDATE", subject_id=source_id,
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale=f"captured bytes could not be normalized: {exc}",
                capability_failure_class="PARSER_INCAPABILITY"))
            continue
        entry["document"] = document
        annotation = analyze_layout(document.text, document.pages,
                                    retrieval.media_type or "text/plain",
                                    document.parser)
        entry["annotation"] = annotation
        layer_failure = reading_order_failure(annotation, subject_id=document.document_id)
        if layer_failure is not None:
            capability_outcomes.append(layer_failure)
            if "EMPTY_TEXT_LAYER" in annotation.warnings:
                entry["document"] = None
                continue

        resolution = resolve_source_roles(
            {key: value for key, value in {
                "publisher": source.publisher,
                "host_domain": _domain(source.final_urls[0]) if source.final_urls else None,
            }.items() if value},
            subject_id=source_id, source_object_id=source_id)
        role_rows.append(resolution.to_record())
        capability_outcomes.extend(resolution.outcomes)

        for candidate in (*deterministic_candidates(case_id, document, source),
                          *repository_mention_candidates(case_id, document, source)):
            parse = parse_semantics(
                candidate.original_text,
                _context_window(document, candidate.span_start, candidate.span_end),
                source.language or document.language)
            result = admit_candidate(candidate, document, annotation, parse)
            expansion = None
            if result.stage == "SEMANTICALLY_PARSED" and result.expansion_request:
                expansion = expand_context(
                    document, candidate.span_start, candidate.span_end,
                    result.expansion_request,
                    document_date=source.publication_time)
            candidates[candidate.candidate_id] = candidate
            admission_rows.append({
                **result.to_record(),
                "expansion_id": expansion.expansion_id if expansion else None})
            if result.capability_failure is not None:
                capability_outcomes.append(result.capability_failure)
            if result.stage == "ACCEPTED_CANDIDATE" and \
                    candidate.candidate_type.startswith("CLAIM"):
                accepted_claim_candidates.append({
                    "candidate": candidate, "parse": parse,
                    "source_id": source_id,
                    "publication_time": source.publication_time})

    # Dependence over all analyzed pairs.
    dependence_rows: list[dict[str, Any]] = []
    pair_assessments: dict[tuple[str, str], Any] = {}
    analyzed = [key for key in sorted(entries) if entries[key]["document"] is not None]
    families: dict[str, str] = {}
    pub_refs: dict[str, PublicationRef] = {}
    for source_id in analyzed:
        source = entries[source_id]["source"]
        families[source_id] = stable_id(
            "family", _domain(source.final_urls[0]) if source.final_urls else source_id)
        pub_refs[source_id] = PublicationRef(
            source_id, families[source_id], source.publisher,
            source.language or "und", source.content_hash)
    for index, left_id in enumerate(analyzed):
        for right_id in analyzed[index + 1:]:
            try:
                assessment = classify_dependence(
                    pub_refs[left_id], pub_refs[right_id],
                    _shared_signals(entries[left_id], entries[right_id]))
            except DependenceCapabilityError as exc:
                capability_outcomes.append(exc.failure)
                continue
            dependence_rows.append(assessment.to_record())
            pair_assessments[(left_id, right_id)] = assessment

    # Claim families -> evidence bases -> claims.
    family_groups: dict[str, list[dict[str, Any]]] = {}
    for item in accepted_claim_candidates:
        parse = item["parse"]
        key_tokens = tuple(sorted(_norm_tokens(parse.subject + " " + parse.predicate)))[:6]
        family_groups.setdefault(stable_id("claim-family", key_tokens), []).append(item)

    bases: list[EvidenceBasis] = []
    claims: list[ClaimUnit] = []
    claim_meta: dict[str, dict[str, Any]] = {}
    family_rows: list[dict[str, Any]] = []
    claim_dependence: dict[str, list[str]] = {}
    for family_id in sorted(family_groups):
        members = sorted(family_groups[family_id],
                         key=lambda item: item["candidate"].candidate_id)
        member_sources = sorted({item["source_id"] for item in members})
        member_pairs = {key: value for key, value in pair_assessments.items()
                        if key[0] in member_sources and key[1] in member_sources}
        states = sorted({assessment.state for assessment in member_pairs.values()})
        basis = evidence_basis(
            case_id=case_id,
            source_object_ids=tuple(member_sources),
            candidate_ids=tuple(item["candidate"].candidate_id for item in members),
            source_family_id=family_id,
            independence_state=_legacy_independence(states))
        bases.append(basis)
        family_rows.append(corroboration_arithmetic(
            claim_family_id=family_id,
            publications=[pub_refs[source_id] for source_id in member_sources],
            assessments=member_pairs.values(),
            evidence_basis_ids=(basis.basis_id,)).to_record())
        for item in members:
            candidate, parse = item["candidate"], item["parse"]
            claim = claim_from_candidate(
                case_id=case_id, candidate=candidate,
                evidence_basis_ids=(basis.basis_id,),
                normalized_statement=" ".join(candidate.original_text.split()),
                subject=parse.subject, predicate=parse.predicate,
                object_or_value=parse.object_or_value,
                modality=parse.modality, polarity=parse.polarity,
                temporal_scope=parse.temporal_scope,
                geographic_scope=parse.geographic_scope,
                epistemic_state="EXTRACTED")
            claims.append(claim)
            claim_meta[claim.claim_id] = item
            claim_dependence[claim.claim_id] = states or ["INDEPENDENCE_UNKNOWN"]

    # Support and relations inside each family.
    support_rows: list[dict[str, Any]] = []
    relation_rows: list[dict[str, Any]] = []
    claims_by_family: dict[str, list[ClaimUnit]] = {}
    for claim in claims:
        claims_by_family.setdefault(claim.evidence_basis_ids[0], []).append(claim)
    for basis_id in sorted(claims_by_family):
        members = sorted(claims_by_family[basis_id], key=lambda claim: claim.claim_id)
        for claim in members:
            evidence_items = [{
                "evidence_id": other.claim_id,
                "subject": other.subject, "predicate": other.predicate,
                "object_or_value": other.object_or_value,
                "polarity": other.polarity, "modality": other.modality,
                "temporal_scope": other.temporal_scope,
                "geographic_scope": other.geographic_scope,
                "entities": (other.subject,),
                "correction_state": "CURRENT", "active": True,
                "source_class": entries[claim_meta[other.claim_id]["source_id"]]
                                ["source"].source_class,
            } for other in members]
            support_rows.append(assess_support(claim, evidence_items).to_record())
        for left_index, left_claim in enumerate(members):
            for right_claim in members[left_index + 1:]:
                frames = (
                    temporal_frame(
                        claim_id=left_claim.claim_id,
                        event_time=left_claim.temporal_scope,
                        publication_time=claim_meta[left_claim.claim_id]["publication_time"]),
                    temporal_frame(
                        claim_id=right_claim.claim_id,
                        event_time=right_claim.temporal_scope,
                        publication_time=claim_meta[right_claim.claim_id]["publication_time"]))
                relation_rows.append(
                    relate(left_claim, right_claim, frames=frames).to_record())

    hypotheses = [hypothesis(
        case_id=case_id,
        statement="The campaign research question is answerable from the admitted "
                  "public evidence without unresolved material dependence.",
        supporting_claim_ids=tuple(claim.claim_id for claim in claims[:8]),
        contradicting_claim_ids=(),
        independent_evidence_count=sum(
            row["independent_corroboration_count"] > 0 for row in family_rows),
        source_dependence_summary="; ".join(sorted(
            {state for states in claim_dependence.values() for state in states}))
            or "NO_PAIRS_ANALYZED",
        assumptions=("public custody is complete for the frozen question",),
        unknowns=tuple(sorted({state for states in claim_dependence.values()
                               for state in states if state == "INDEPENDENCE_UNKNOWN"})),
        disconfirming_evidence_requirement="an official correction or retraction "
                                           "of the load-bearing claims",
    ).to_record()]

    support_audit = audit_support_set(support_rows)
    for name, rows in (
            ("candidate_register.jsonl", [item.to_record() for item in candidates.values()]),
            ("admission_register.jsonl", admission_rows),
            ("role_register.jsonl", role_rows),
            ("dependence_register.jsonl", dependence_rows),
            ("claim_family_register.jsonl", family_rows),
            ("claim_support_register.jsonl", support_rows),
            ("relation_register.jsonl", relation_rows),
            ("capability_outcome_register.jsonl",
             [item.to_record() for item in capability_outcomes])):
        (out / name).unlink(missing_ok=True)
        append_jsonl(out / name, rows)
    write_json(out / "claim_register.json", [claim.to_record() for claim in claims])
    write_json(out / "evidence_basis_register.json",
               [basis.to_record() for basis in bases])
    write_json(out / "source_register.json",
               [entries[key]["source"].to_record() for key in sorted(entries)])
    write_json(out / "contradiction_register.json", relation_rows)
    write_json(out / "hypothesis_register.json", hypotheses)
    write_json(out / "claim_dependence_states.json",
               {key: sorted(value) for key, value in claim_dependence.items()})

    metrics = {
        "campaign_key": campaign_key,
        "sources_captured": len(sources), "sources_analyzed": len(analyzed),
        "candidates": len(candidates),
        "admissions_by_stage": dict(Counter(row["stage"] for row in admission_rows)),
        "claims": len(claims),
        "dependence_pairs": len(dependence_rows),
        "dependence_by_state": dict(Counter(row["state"] for row in dependence_rows)),
        "claim_families": len(family_rows),
        "support_audit": support_audit,
        "relations": len(relation_rows),
        "capability_failures": sum(
            item.outcome == "SYSTEM_CAPABILITY_FAILURE" for item in capability_outcomes),
        "epistemically_unresolvable": sum(
            item.outcome == "EPISTEMICALLY_UNRESOLVABLE" for item in capability_outcomes),
    }
    metrics["integrity_hash"] = sha256(metrics)
    write_json(out / "analysis_metrics.json", metrics)
    return metrics


# ---------------------------------------------------------------------------
# Section 26 — reports
# ---------------------------------------------------------------------------

_QUALIFICATION_WORDING = {
    "PLANNED": "planned", "PROPOSED": "proposed", "INTENDED": "intended",
    "REPORTED": "reported", "VENDOR_DESCRIBED": "vendor-described",
    "EXERCISED": "exercised", "PILOTED": "piloted", "DEPLOYED": "deployed",
    "OPERATIONAL": "operational", "DISPUTED": "disputed",
    "PRELIMINARY": "preliminary", "FINAL": "final", "CORRECTED": "corrected",
    "SUPERSEDED": "superseded", "UNRESOLVED": "unresolved",
}


def _render_sentence(claim: Mapping[str, Any]) -> str:
    text = " ".join(str(claim["normalized_statement"]).split()).rstrip(".")
    marker = claim["modality"] if claim["modality"] in MATERIAL_QUALIFICATION_MARKERS else None
    if marker:
        text = f"{text} (status: {_QUALIFICATION_WORDING[marker]})"
    return text + "."


def _load_claim(record: Mapping[str, Any]) -> ClaimUnit:
    value = dict(record)
    value["temporal_scope"] = tuple(value["temporal_scope"])
    for key in ("geographic_scope", "evidence_basis_ids", "candidate_ids"):
        value[key] = tuple(value[key])
    return ClaimUnit(**value)


def build_reports(case: Mapping[str, Any], analysis_root: str | Path,
                  output_root: str | Path) -> dict[str, Any]:
    analysis = Path(analysis_root)
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    claims = [_load_claim(record) for record in read_json(analysis / "claim_register.json")]
    claim_records = {claim.claim_id: claim for claim in claims}
    support_rows = read_jsonl(analysis / "claim_support_register.jsonl")
    dependence_states = read_json(analysis / "claim_dependence_states.json")
    hypotheses = read_json(analysis / "hypothesis_register.json")
    contradiction_rows = read_json(analysis / "contradiction_register.json")
    basis_records = read_json(analysis / "evidence_basis_register.json")
    source_records = read_json(analysis / "source_register.json")
    candidate_records = read_jsonl(analysis / "candidate_register.jsonl")
    candidate_source = {record["candidate_id"]: record["source_object_id"]
                       for record in candidate_records}
    support_by_claim = {row["claim_id"]: row for row in support_rows}

    propositions = []
    packages = []
    refusals = []
    published_rows = []
    published_ids = []
    omission_reasons: dict[str, str] = {}
    for claim in sorted(claims, key=lambda item: item.claim_id):
        support = support_by_claim.get(claim.claim_id)
        states = dependence_states.get(claim.claim_id, ["INDEPENDENCE_UNKNOWN"])
        if support is None or support["state"] not in {
                "FULL_SUPPORT", "QUALIFIED_SUPPORT", "PARTIAL_SUPPORT"}:
            refusals.append({
                "claim_id": claim.claim_id,
                "refusal_reason": "SUPPORT_STATE_BELOW_PUBLICATION_THRESHOLD",
                "support_state": support["state"] if support else "UNASSESSED"})
            continue
        rendered = _render_sentence(claim.to_record())
        # Pre-render overreach screen: the v4 validator's epistemic-language
        # rule is applied BEFORE publication so an overreaching source
        # sentence is refused, never rendered and then flagged.
        from ..v4.reporting import _epistemic_overreach
        if _epistemic_overreach(rendered, claim.epistemic_state):
            refusals.append({
                "claim_id": claim.claim_id,
                "refusal_reason": "EPISTEMIC_LANGUAGE_OVERREACH",
                "support_state": support["state"]})
            continue
        decomposition = decompose_sentence(rendered, [claim.to_record()])
        if decomposition.status != "DECOMPOSED":
            refusals.append({"claim_id": claim.claim_id,
                             "refusal_reason": "REQUIRES_SPLIT",
                             "unsupported_segments": list(decomposition.unsupported_segments)})
            continue
        proposition = decomposition.propositions[0]
        violations = list(independence_language_guard(rendered, states))
        violations += list(qualification_preservation_check(proposition, rendered))
        propositions.append(proposition)
        if violations:
            refusals.append({
                "claim_id": claim.claim_id, "refusal_reason": "FAITHFULNESS_VIOLATION",
                "codes": sorted({violation.code for violation in violations})})
            omission_reasons[proposition.proposition_id] = "UNSUPPORTED"
            packages.append(proposition_evidence_package(
                proposition=proposition, disposition="REFUSED_UNSUPPORTED",
                omission_reason="faithfulness guard violation: "
                                + ", ".join(sorted({v.code for v in violations})),
            ).to_record())
            continue
        published_ids.append(proposition.proposition_id)
        published_rows.append((claim, proposition, rendered))
        disposition = ("PUBLISHED_WITH_QUALIFICATION"
                       if proposition.qualifications else "PUBLISHED_FAITHFULLY")
        packages.append(proposition_evidence_package(
            proposition=proposition, disposition=disposition,
            final_sentence=rendered).to_record())

    exec_props = [proposition for _claim, proposition, _rendered in published_rows[:3]]
    exec_violations = (tuple(validate_executive_summary(exec_props, propositions))
                       if propositions else ())
    omission = material_omission_audit(propositions, tuple(published_ids),
                                       omission_reasons)

    sentences = []
    for claim, _proposition, rendered in published_rows:
        source_ids = tuple(sorted({candidate_source[candidate_id]
                                   for candidate_id in claim.candidate_ids
                                   if candidate_id in candidate_source}))
        sentences.append(report_sentence(
            section="KEY_FINDINGS", text=rendered, sentence_type="FACTUAL",
            claim_ids=(claim.claim_id,), evidence_basis_ids=claim.evidence_basis_ids,
            source_object_ids=source_ids, epistemic_state=claim.epistemic_state))
    for section, text in (
            ("EXECUTIVE_ASSESSMENT",
             "This research-shadow report presents extracted public-source findings; "
             "source independence is stated only where affirmatively supported."),
            ("INVESTIGATION_QUESTION_AND_SCOPE", str(case.get("research_question",
             "Frozen campaign research question on public record evolution."))),
            ("METHOD_AND_LIMITS",
             "Deterministic acquisition, normalization, extraction and dependence "
             "analysis over immutable public custody; model-assisted review only."),
            ("TIMELINE", "Event and publication times are recorded separately "
             "in the claim registers."),
            ("ENTITIES_AND_PROGRAMMES", "Entity identities are resolved only with "
             "explicit identity evidence; ambiguous identities remain explicit."),
            ("EVIDENCE_BASIS_AND_SOURCE_DEPENDENCE_ANALYSIS",
             "Dependence classifications for every analyzed publication pair are "
             "recorded in the dependence register; no dependence finding was "
             "upgraded to independence without positive evidence."),
            ("CONTRADICTIONS_CORRECTIONS_AND_RETRACTIONS",
             "Relation classifications including corrections and supersessions "
             "are recorded in the contradiction register."),
            ("COMPETING_HYPOTHESES", "Competing hypotheses remain open in the "
             "hypothesis register without a forced winner."),
            ("OPERATIONAL_OR_STRATEGIC_IMPLICATIONS",
             "No operational or strategic implication is asserted beyond the "
             "supported claims."),
            ("UNKNOWNS_AND_EVIDENCE_GAPS",
             "Unresolved dependence and unresolved relations remain explicitly "
             "recorded rather than resolved by assumption."),
            ("SOURCE_REGISTER", "All captured sources are listed in the source "
             "register with custody hashes."),
            ("REPRODUCIBILITY_AND_INTEGRITY",
             "All registers carry deterministic integrity hashes and replay from "
             "copied custody without network access."),
            ("REVIEW_STATUS", "HUMAN REVIEW PENDING: no human validation is "
             "claimed for any finding in this report.")):
        sentences.append(report_sentence(
            section=section, text=text,
            sentence_type="REVIEW_STATUS" if section == "REVIEW_STATUS" else "METHOD"))
    sentences.append(report_sentence(
        section="WHAT_CURUNIR_REFUSES_TO_CONCLUDE",
        text="Curunir refuses to conclude source independence, validated accuracy, "
             "or operational readiness from the reviewed material.",
        sentence_type="REFUSAL"))

    bases = [EvidenceBasis(**{**record,
                              "source_object_ids": tuple(record["source_object_ids"]),
                              "candidate_ids": tuple(record["candidate_ids"])})
             for record in basis_records]
    sources = [_load_source(record) for record in source_records]
    validation = validate_report(
        sentences=sentences, claims=claims, bases=bases, sources=sources)
    hashes = render_report(
        case={"case_id": str(case.get("case_id", "case-unknown")),
              "title": str(case.get("title", "Curunir V5.1 campaign report")),
              "integrity_hash": sha256(dict(case))},
        sentences=sentences, sources=sources, claims=claims, bases=bases,
        hypotheses=hypotheses, contradictions=contradiction_rows,
        validation=validation, output_dir=out)

    (out / "proposition_evidence_ledger.jsonl").unlink(missing_ok=True)
    append_jsonl(out / "proposition_evidence_ledger.jsonl", packages)
    write_json(out / "refusal_register.json", refusals)
    write_json(out / "dependence_register.json",
               read_jsonl(analysis / "dependence_register.jsonl"))
    summary = {
        "campaign_key": case.get("campaign_key"),
        "reportable_propositions": len(propositions),
        "published_propositions": len(published_ids),
        "refused": len(refusals),
        "executive_summary_violations": [item.to_record() for item in exec_violations],
        "material_omission_audit": omission.to_record(),
        "report_validation_verdict": validation["verdict"],
        "report_hashes": hashes,
    }
    write_json(out / "report_build_summary.json", summary)
    return summary


# ---------------------------------------------------------------------------
# CLI wrappers (names fixed by cli.FORWARDED_COMMANDS)
# ---------------------------------------------------------------------------

def candidate_scan_record(**spec: Any) -> dict[str, Any]:
    return record_candidate_scan(**spec)


def freeze_campaigns(**spec: Any) -> dict[str, Any]:
    return freeze_campaign_definitions(**spec)


def acquire(**spec: Any) -> dict[str, Any]:
    definition = spec["definition"]
    planned = plan_acquisition(definition, spec["leads"])
    return execute_acquisition(definition, planned, spec["custody_root"])


def analyze(**spec: Any) -> dict[str, Any]:
    return analyze_campaign(spec["custody_root"], spec["output_root"],
                            spec["campaign_key"])


def report(**spec: Any) -> dict[str, Any]:
    return build_reports(spec["case"], spec["analysis_root"], spec["output_root"])

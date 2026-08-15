"""Campaign analysis, evidence-bound products, and offline deterministic replay."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .analysis import (
    ai_candidate_from_located_text, analysis_metrics, deterministic_candidates, disagreements,
    entity_record, evidence_basis, hypothesis, propose_identity, relate_claims,
    repository_mention_candidates, source_origin_edge,
)
from .custody import create_translation, normalize_source
from .io import append_jsonl, read_json, read_jsonl, write_json
from .kernel import (
    ZeroWriteMonitor, build_proposal, inspect_frozen_kernel, shadow_dry_run,
    static_zero_write_scan, validate_proposal, zero_write_guard,
)
from .mission import build_handoff
from .models import (
    ClaimRelation, ClaimUnit, DerivativeMapping, EvidenceBasis, ExtractionCandidate,
    InferenceRecord, InvestigationHypothesis, NormalizedDocument, RetrievalRecord,
    SentenceEvidence, SourceOriginEdge, SourceRecord, TranslationDerivative, sha256, stable_id,
)
from .reporting import render_report, sentence, validate_report


def _source(record: Mapping[str, Any]) -> SourceRecord:
    value = dict(record)
    for key in ("retrieval_ids", "requested_urls", "final_urls"):
        value[key] = tuple(value[key])
    return SourceRecord(**value)


def _document(record: Mapping[str, Any]) -> NormalizedDocument:
    value = dict(record)
    value["sections"] = tuple(tuple(item) for item in value["sections"])
    value["pages"] = tuple(tuple(item) for item in value["pages"])
    value["mappings"] = tuple(DerivativeMapping(**item) for item in value["mappings"])
    value["warnings"] = tuple(value["warnings"]); value["omitted_content"] = tuple(value["omitted_content"])
    return NormalizedDocument(**value)


def _candidate(record: Mapping[str, Any]) -> ExtractionCandidate:
    value = dict(record); value["warnings"] = tuple(value["warnings"])
    return ExtractionCandidate(**value)


def _url_map(sources: tuple[SourceRecord, ...]) -> dict[str, SourceRecord]:
    return {url: source for source in sources for url in source.requested_urls}


def assemble_campaign(*, case: Mapping[str, Any], capture_root: str | Path,
                      analysis_spec: Mapping[str, Any], analysis_output: str | Path,
                      report_output: str | Path, admission_output: str | Path,
                      repository_root: str | Path) -> dict[str, Any]:
    capture = Path(capture_root); analysis_dir = Path(analysis_output); admission_dir = Path(admission_output)
    analysis_dir.mkdir(parents=True, exist_ok=True); admission_dir.mkdir(parents=True, exist_ok=True)
    sources = tuple(_source(item) for item in read_json(capture / "custody" / "source_object_manifest.json"))
    documents = tuple(_document(item) for item in read_json(capture / "custody" / "normalization_manifest.json"))
    by_url = _url_map(sources); by_source_doc = {item.source_object_id: item for item in documents}

    candidates: list[ExtractionCandidate] = []
    provider_invocations = {"deterministic_baseline": 0, "repository_extractor": 0, "ai_assisted": 0}
    for source in sources:
        document = by_source_doc.get(source.source_object_id)
        if not document: continue
        candidates.extend(deterministic_candidates(str(case["case_id"]), document, source))
        provider_invocations["deterministic_baseline"] += 1
        candidates.extend(repository_mention_candidates(str(case["case_id"]), document, source))
        provider_invocations["repository_extractor"] += 1
    candidate_alias: dict[str, ExtractionCandidate] = {}
    for spec in analysis_spec.get("ai_candidates", ()):
        source = by_url[str(spec["source_url"])]; document = by_source_doc[source.source_object_id]
        item = ai_candidate_from_located_text(
            case_id=str(case["case_id"]), document=document, source=source,
            exact_text=str(spec["exact_text"]), candidate_type=str(spec.get("candidate_type", "CLAIM_CANDIDATE")),
            normalized_value=dict(spec.get("normalized_value", {})), occurrence=int(spec.get("occurrence", 1)),
        )
        candidates.append(item); candidate_alias[str(spec["alias"])] = item
        provider_invocations["ai_assisted"] += 1
    disagreement_values = disagreements(candidates)
    append_jsonl(analysis_dir / "candidate_register.jsonl", (item.to_record() for item in candidates))
    write_json(analysis_dir / "candidate_disagreements.json", [item.to_record() for item in disagreement_values])
    write_json(analysis_dir / "provider_invocations.json", provider_invocations)

    translations: list[TranslationDerivative] = []
    for spec in analysis_spec.get("translations", ()):
        source = by_url[str(spec["source_url"])]; document = by_source_doc[source.source_object_id]
        translations.append(create_translation(
            document, target_language=str(spec.get("target_language", "en")),
            translated_text=str(spec["translated_text"]), provider="SOL_AI_SECONDARY_TRANSLATION",
            provider_version="sol-2026-07-22", alignment_precision="APPROXIMATE_SECTION",
            warnings=("ADVISORY_TRANSLATION_ORIGINAL_REMAINS_AUTHORITATIVE",),
        ))
    write_json(analysis_dir / "translation_manifest.json", [item.to_record() for item in translations])

    entities = {str(spec["alias"]): entity_record(
        str(spec["entity_class"]), str(spec["canonical_name"]), aliases=tuple(spec.get("aliases", ())),
        external_identifiers=dict(spec.get("external_identifiers", {})),
    ) for spec in analysis_spec.get("entities", ())}
    identity_values = []
    for spec in analysis_spec.get("identity_proposals", ()):
        identity_values.append(propose_identity(
            entities[str(spec["left"])], entities[str(spec["right"])],
            evidence_candidate_ids=tuple(candidate_alias[name].candidate_id for name in spec.get("candidate_aliases", ())),
            factors=dict(spec.get("factors", {})), accepted_by_human=False,
        ))
    write_json(analysis_dir / "entity_register.json", [item.to_record() for item in entities.values()])
    write_json(analysis_dir / "identity_proposals.json", [item.to_record() for item in identity_values])

    origin_values: list[SourceOriginEdge] = []
    for spec in analysis_spec.get("source_origin_edges", ()):
        origin_values.append(source_origin_edge(
            source_id=by_url[str(spec["source_url"])].source_object_id,
            target_id=by_url[str(spec["target_url"])].source_object_id,
            relationship=str(spec["relationship"]),
            evidence_candidate_ids=tuple(candidate_alias[name].candidate_id for name in spec.get("candidate_aliases", ())),
            metadata_basis=tuple(spec.get("metadata_basis", ())), provider=str(spec.get("provider", "SOL_AI_SECONDARY_REVIEW")),
            review_state="HUMAN_REVIEW_PENDING",
        ))
    for translation, spec in zip(translations, analysis_spec.get("translations", ())):
        origin_values.append(source_origin_edge(
            source_id=translation.translation_id, target_id=by_url[str(spec["source_url"])].source_object_id,
            relationship="TRANSLATED_FROM", metadata_basis=(translation.translation_id,),
            provider="SOL_AI_SECONDARY_TRANSLATION", review_state="AI_SECONDARY_REVIEW",
        ))
    write_json(analysis_dir / "source_origin_graph.json", [item.to_record() for item in origin_values])

    bases: dict[str, EvidenceBasis] = {}
    for spec in analysis_spec.get("evidence_bases", ()):
        alias = str(spec["alias"])
        bases[alias] = evidence_basis(
            case_id=str(case["case_id"]),
            source_object_ids=tuple(by_url[url].source_object_id for url in spec["source_urls"]),
            candidate_ids=tuple(candidate_alias[name].candidate_id for name in spec["candidate_aliases"]),
            source_family_id=str(spec["source_family_id"]), independence_state=str(spec["independence_state"]),
            active=bool(spec.get("active", True)), correction_state=str(spec.get("correction_state", "CURRENT")),
        )
    claims: dict[str, ClaimUnit] = {}
    for spec in analysis_spec.get("claims", ()):
        candidate = candidate_alias[str(spec["candidate_alias"])]
        alias = str(spec["alias"])
        claims[alias] = ClaimUnit(
            stable_id("claim", case["case_id"], alias, candidate.candidate_id), str(case["case_id"]),
            str(spec["normalized_statement"]), candidate.original_text, str(spec["subject"]),
            str(spec["predicate"]), str(spec["object_or_value"]), tuple(spec.get("temporal_scope", (None, None))),
            tuple(spec.get("geographic_scope", ())), str(spec.get("modality", "ASSERTED")),
            str(spec.get("polarity", "POSITIVE")), str(spec.get("epistemic_state", "DIRECTLY_STATED")),
            tuple(bases[name].basis_id for name in spec["basis_aliases"]), (candidate.candidate_id,),
            "HUMAN_REVIEW_PENDING", 1,
        )
    relations: list[ClaimRelation] = []
    for spec in analysis_spec.get("claim_relations", ()):
        relations.append(relate_claims(
            claims[str(spec["left"])], claims[str(spec["right"])], str(spec["relation_type"]),
            evidence_candidate_ids=tuple(candidate_alias[name].candidate_id for name in spec.get("candidate_aliases", ())),
            temporal_relationship=str(spec.get("temporal_relationship", "UNKNOWN")),
            scope_relationship=str(spec.get("scope_relationship", "SAME_SCOPE")),
            provider=str(spec.get("provider", "SOL_AI_SECONDARY_REVIEW")),
        ))
    hypothesis_values: dict[str, InvestigationHypothesis] = {}
    for spec in analysis_spec.get("hypotheses", ()):
        hypothesis_values[str(spec["alias"])] = hypothesis(
            case_id=str(case["case_id"]), statement=str(spec["statement"]),
            supporting_claim_ids=tuple(claims[name].claim_id for name in spec.get("supporting_claim_aliases", ())),
            contradicting_claim_ids=tuple(claims[name].claim_id for name in spec.get("contradicting_claim_aliases", ())),
            independent_evidence_count=int(spec["independent_evidence_count"]),
            source_dependence_summary=str(spec["source_dependence_summary"]),
            assumptions=tuple(spec.get("assumptions", ())), unknowns=tuple(spec.get("unknowns", ())),
            disconfirming_evidence_requirement=str(spec["disconfirming_evidence_requirement"]),
            status=str(spec.get("status", "UNRESOLVED")),
        )
    inferences: dict[str, InferenceRecord] = {}
    for spec in analysis_spec.get("inferences", ()):
        alias = str(spec["alias"]); premise_ids = tuple(claims[name].claim_id for name in spec["premise_claim_aliases"])
        basis_ids = tuple(bases[name].basis_id for name in spec["basis_aliases"])
        inferences[alias] = InferenceRecord(stable_id("inference", case["case_id"], alias, premise_ids),
                                             str(spec["conclusion"]), premise_ids, basis_ids,
                                             str(spec["rule_or_provider"]), tuple(spec.get("assumptions", ())),
                                             tuple(spec.get("alternatives", ())), str(spec["uncertainty"]),
                                             tuple(spec.get("prohibited_overreach", ())))
    write_json(analysis_dir / "evidence_basis_register.json", [item.to_record() for item in bases.values()])
    write_json(analysis_dir / "claim_graph.json", {"claims": [item.to_record() for item in claims.values()],
                                                    "relations": [item.to_record() for item in relations]})
    write_json(analysis_dir / "contradiction_register.json", [item.to_record() for item in relations
                                                               if item.relation_type != "SUPPORT"])
    write_json(analysis_dir / "hypothesis_register.json", [item.to_record() for item in hypothesis_values.values()])
    write_json(analysis_dir / "inference_register.json", [item.to_record() for item in inferences.values()])

    sentence_values: list[SentenceEvidence] = []
    for spec in analysis_spec.get("report_sentences", ()):
        kind = str(spec["sentence_type"]); claim_ids = tuple(claims[name].claim_id for name in spec.get("claim_aliases", ()))
        basis_ids = tuple(bases[name].basis_id for name in spec.get("basis_aliases", ()))
        source_ids = tuple(by_url[url].source_object_id for url in spec.get("source_urls", ()))
        inference_id = inferences[str(spec["inference_alias"])].inference_id if spec.get("inference_alias") else None
        sentence_values.append(sentence(section=str(spec["section"]), text=str(spec["text"]), sentence_type=kind,
                                        claim_ids=claim_ids, evidence_basis_ids=basis_ids,
                                        source_object_ids=source_ids,
                                        epistemic_state=str(spec.get("epistemic_state", "UNKNOWN")),
                                        inference_id=inference_id))
    validation = validate_report(sentences=sentence_values, claims=claims.values(), bases=bases.values(),
                                 sources=sources, candidates=candidates, inferences=inferences.values())
    if validation["verdict"] != "PASS":
        raise ValueError(f"report support validation failed: {validation['findings']}")
    report_hashes = render_report(case=case, sentences=sentence_values, sources=sources, claims=claims.values(),
                                  bases=bases.values(), hypotheses=(item.to_record() for item in hypothesis_values.values()),
                                  contradictions=(item.to_record() for item in relations), validation=validation,
                                  output_dir=report_output)
    write_json(Path(report_output) / "report_support_validation.json", validation)

    handoffs = []
    for spec in analysis_spec.get("handoffs", ()):
        ids = tuple((claims[name].claim_id if name in claims else hypothesis_values[name].hypothesis_id)
                    for name in spec["claim_or_hypothesis_aliases"])
        handoffs.append(build_handoff(
            case_id=str(case["case_id"]), claim_or_hypothesis_ids=ids,
            evidence_basis_ids=tuple(bases[name].basis_id for name in spec["basis_aliases"]),
            source_dependence_summary=str(spec["source_dependence_summary"]),
            correction_retraction_state=str(spec["correction_retraction_state"]),
            uncertainty=str(spec["uncertainty"]), proposed_implication=str(spec["proposed_implication"]),
            recipient=str(spec["recipient"]), action_kind=str(spec["action_kind"]),
            access_marking=dict(spec.get("access_marking", {"releasability": ["PUBLIC"]})),
            expires_at=str(spec["expires_at"]),
        ))
    write_json(analysis_dir / "mission_handoffs.json", [item.to_record() for item in handoffs])

    inspection = inspect_frozen_kernel(repository_root); monitor = ZeroWriteMonitor(repository_root)
    proposals = []; validations = []
    with zero_write_guard(monitor):
        for spec in analysis_spec.get("kernel_proposals", ()):
            item = build_proposal(
                case_id=str(case["case_id"]), proposed_concept=str(spec["proposed_concept"]),
                target_table_or_action=str(spec["target_table_or_action"]),
                proposed_field_values=dict(spec["proposed_field_values"]),
                source_object_ids=tuple(by_url[url].source_object_id for url in spec["source_urls"]),
                claim_ids=tuple(claims[name].claim_id for name in spec["claim_aliases"]),
                evidence_basis_ids=tuple(bases[name].basis_id for name in spec["basis_aliases"]),
                source_independence_state=str(spec["source_independence_state"]),
                identity_state=str(spec["identity_state"]), contradiction_state=str(spec["contradiction_state"]),
                correction_retraction_state=str(spec["correction_retraction_state"]),
                mapping_precision=str(spec["mapping_precision"]), dependencies=tuple(spec.get("dependencies", ())),
                provider=str(spec.get("provider", "SOL_AI_SECONDARY_REVIEW")),
                creator_actor_id="strategic-analyst-v4", access_marking=dict(spec.get("access_marking", {"releasability": ["PUBLIC"]})),
            )
            proposals.append(item)
            validations.append(validate_proposal(
                item, inspection, validator_node_id="KERNEL_REVIEW_NODE", reviewer_actor_id="kernel-reviewer-v4",
                known_claim_ids={value.claim_id for value in claims.values()},
                known_basis_ids={value.basis_id for value in bases.values()},
                known_source_ids={value.source_object_id for value in sources},
                known_dependencies=set(spec.get("known_dependencies", ())),
            ))
        shadow = shadow_dry_run(proposals, validations, dict(analysis_spec.get("frozen_kernel_fixture", {})), inspection)
    zero_write = monitor.verify(); zero_write["static_scan"] = static_zero_write_scan(Path(repository_root) / "curunir_operational" / "v4")
    write_json(admission_dir / "kernel_inspection.json", inspection)
    write_json(admission_dir / "kernel_admission_policy.json", {
        "version": "curunir-kernel-admission-shadow-policy-v4", "human_review_required": True,
        "provider_self_approval": False, "canonical_write_authority": False,
    })
    write_json(admission_dir / "proposal_packets.json", [item.to_record() for item in proposals])
    write_json(admission_dir / "validation_reports.json", [item.to_record() for item in validations])
    write_json(admission_dir / "kernel_shadow_diff.json", shadow.to_record())
    write_json(admission_dir / "zero_write_evidence.json", zero_write)

    metrics = analysis_metrics(sources=sources, candidates=candidates, identities=identity_values,
                               origin_edges=origin_values, bases=bases.values(), claims=claims.values(), relations=relations)
    metrics.update({"provider_invocations": provider_invocations, "provider_disagreements": len(disagreement_values),
                    "translations": len(translations), "hypotheses": len(hypothesis_values),
                    "report": validation, "report_hashes": report_hashes, "handoffs": len(handoffs),
                    "kernel_proposals": len(proposals),
                    "kernel_statuses": {status: sum(item.resulting_status == status for item in validations)
                                        for status in sorted({item.resulting_status for item in validations})},
                    "canonical_write_attempts": zero_write["canonical_write_attempts"],
                    "canonical_writes": zero_write["canonical_writes"]})
    metrics["integrity_hash"] = sha256(metrics)
    write_json(analysis_dir / "analysis_metrics.json", metrics)
    write_json(analysis_dir / "analysis_spec_capture.json", dict(analysis_spec))
    return metrics


def replay_campaign(*, case: Mapping[str, Any], capture_root: str | Path,
                    live_analysis_root: str | Path, live_report_root: str | Path,
                    replay_root: str | Path) -> dict[str, Any]:
    """Rebuild derivatives and report from custody and persisted provider records only."""
    capture = Path(capture_root); live_analysis = Path(live_analysis_root); replay = Path(replay_root)
    replay.mkdir(parents=True, exist_ok=True)
    # No network or provider interface is called here. Re-normalize immutable custody bytes.
    retrieval_map = {item["retrieval_id"]: RetrievalRecord(**item)
                     for item in read_jsonl(capture / "custody" / "records" / "retrieval_records.jsonl")}
    sources = tuple(_source(item) for item in read_json(capture / "custody" / "source_object_manifest.json"))
    # Older V4 manifests retain their repository-relative custody path.  Replay
    # must be relocatable: resolve the same immutable hash inside the supplied
    # capture root without rewriting the historical manifest.
    relocated_sources = []
    for source in sources:
        recorded = Path(source.content_path)
        local = (capture / "custody" / "content" / "sha256" / source.content_hash[:2]
                 / source.content_hash[2:4] / source.content_hash)
        selected = recorded if recorded.is_file() else local
        if not selected.is_file():
            raise FileNotFoundError(f"custody object unavailable for {source.source_object_id}")
        if sha256(selected.read_bytes()) != source.content_hash:
            raise ValueError("relocated custody hash mismatch")
        relocated_sources.append(replace(source, content_path=str(selected)))
    sources = tuple(relocated_sources)
    derivative_hashes = []
    for source in sources:
        retrieval = retrieval_map[source.retrieval_ids[0]]
        derivative_hashes.append(normalize_source(source, retrieval).derivative_hash)
    live_documents = read_json(capture / "custody" / "normalization_manifest.json")
    live_derivatives = sorted(item["derivative_hash"] for item in live_documents)
    if sorted(derivative_hashes) != live_derivatives:
        raise ValueError("offline derivative replay mismatch")
    claims = tuple(ClaimUnit(**item) for item in read_json(live_analysis / "claim_graph.json")["claims"])
    bases = tuple(EvidenceBasis(**item) for item in read_json(live_analysis / "evidence_basis_register.json"))
    hypotheses = read_json(live_analysis / "hypothesis_register.json")
    contradictions = read_json(live_analysis / "claim_graph.json")["relations"]
    inferences = tuple(InferenceRecord(**item) for item in read_json(live_analysis / "inference_register.json"))
    sentences = tuple(SentenceEvidence(**item) for item in read_jsonl(Path(live_report_root) / "sentence_evidence_ledger.jsonl"))
    candidates = tuple(_candidate(item) for item in read_jsonl(live_analysis / "candidate_register.jsonl"))
    validation = validate_report(sentences=sentences, claims=claims, bases=bases, sources=sources,
                                 candidates=candidates, inferences=inferences)
    report_hashes = render_report(case=case, sentences=sentences, sources=sources, claims=claims, bases=bases,
                                  hypotheses=hypotheses, contradictions=contradictions, validation=validation,
                                  output_dir=replay / "report")
    live_hashes = read_json(Path(live_report_root) / "investigation_report.json")
    comparison = {
        "network_requests": 0, "provider_reinvocations": 0,
        "derivative_hashes_match": True, "report_validation": validation["verdict"],
        "markdown_hash_match": report_hashes["report_markdown"] == live_hashes["markdown_sha256"],
        "text_hash_match": report_hashes["report_text"] == live_hashes["text_sha256"],
        "mission_handoff_hash": sha256(read_json(live_analysis / "mission_handoffs.json")),
        "replay_mode": "CAPTURED_RETRIEVAL_AND_PROVIDER_RECORDS_ONLY",
    }
    comparison["verdict"] = "PASS" if all((comparison["derivative_hashes_match"],
                                             comparison["markdown_hash_match"], comparison["text_hash_match"],
                                             validation["verdict"] == "PASS")) else "INVALID"
    comparison["integrity_hash"] = sha256(comparison)
    write_json(replay / "replay_report.json", comparison)
    return comparison

"""Executable bounded discovery-capture and immutable acquisition campaign path."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .custody import acquire_public_source, normalize_source
from .investigation import DiscoveryBudget, decide_access
from .io import append_jsonl, write_json
from .models import AccessDecision, InvestigationCase, RetrievalRecord, SearchLead, SourceRecord, sha256
from .io import read_json, read_jsonl


def load_case(record: Mapping[str, Any]) -> InvestigationCase:
    payload = dict(record)
    for key in ("scope", "exclusions", "geographic_scope", "temporal_scope", "languages",
                "entities_of_interest", "source_classes", "evidence_requirements",
                "prohibited_inference_classes", "stop_rules"):
        payload[key] = tuple(payload[key])
    return InvestigationCase(**payload)


def run_live_capture(*, case: InvestigationCase, discovery_spec: Mapping[str, Any],
                     output_root: str | Path) -> dict[str, Any]:
    """Persist captured provider results as leads, then acquire independently."""
    root = Path(output_root); discovery_root = root / "discovery"; custody_root = root / "custody"
    discovery_root.mkdir(parents=True, exist_ok=True); custody_root.mkdir(parents=True, exist_ok=True)
    budget = DiscoveryBudget(case); all_leads = []; query_rows = []
    metadata_by_url: dict[str, Mapping[str, Any]] = {}
    for spec in discovery_spec.get("queries", ()):
        query = budget.issue(
            subquestion_id=str(spec["subquestion_id"]), formulation=str(spec["formulation"]),
            language=str(spec["language"]), provider=str(spec["provider"]),
            provider_category=str(spec["provider_category"]), reason=str(spec["reason"]),
            expected_source_class=str(spec["expected_source_class"]),
            execution_time=spec.get("execution_time"),
        )
        results = tuple(dict(item) for item in spec.get("results", ()))
        for row in results: metadata_by_url[str(row["url"])] = row
        leads = budget.admit_results(query, results); all_leads.extend(leads)
        query_rows.append(budget.queries[-1].to_record())
    run = budget.finish(str(discovery_spec.get("stop_reason", "BOUNDED_CAPTURE_SPEC_COMPLETE")))
    write_json(discovery_root / "discovery_run.json", run.to_record())
    append_jsonl(discovery_root / "discovery_queries.jsonl", query_rows)
    append_jsonl(discovery_root / "search_leads.jsonl", (item.to_record() for item in all_leads))
    write_json(discovery_root / "captured_provider_record.json", dict(discovery_spec))

    access_records = []; retrievals = []; sources_by_id = {}; documents = []; network_requests = 0
    for lead in all_leads[:case.acquisition_budget]:
        metadata = metadata_by_url[lead.url]
        flags = dict(metadata.get("access_flags", {}))
        decision = decide_access(case, lead, **flags)
        access_records.append(decision.to_record())
        if decision.state == "ALLOW_PUBLIC_RETRIEVAL": network_requests += 1
        retrieval, source = acquire_public_source(
            case_id=case.case_id, lead=lead, decision=decision, custody_root=custody_root,
            publisher=str(metadata.get("publisher", lead.apparent_source_authority)),
            source_class=str(metadata.get("source_class", "SECONDARY")),
            title=str(metadata.get("title", lead.title)),
            publication_time=metadata.get("publication_time"),
            timeout_seconds=float(discovery_spec.get("timeout_seconds", 25.0)),
            maximum_bytes=int(discovery_spec.get("maximum_bytes", 20_000_000)),
        )
        retrievals.append(retrieval)
        if source is not None:
            prior = sources_by_id.get(source.source_object_id)
            if prior is None:
                sources_by_id[source.source_object_id] = source
            # Separate publication/retrieval records remain in custody even where content bytes deduplicate.
            try:
                document = normalize_source(source, retrieval, output_dir=custody_root / "normalized")
                documents.append(document)
            except Exception as exc:
                append_jsonl(custody_root / "records" / "normalization_failures.jsonl", ({
                    "retrieval_id": retrieval.retrieval_id, "source_object_id": source.source_object_id,
                    "category": type(exc).__name__, "detail": str(exc)[:300],
                },))
    append_jsonl(discovery_root / "access_decisions.jsonl", access_records)
    sources = tuple(sources_by_id.values())
    write_json(custody_root / "source_object_manifest.json", [item.to_record() for item in sources])
    write_json(custody_root / "normalization_manifest.json", [item.to_record() for item in documents])
    failures = [item for item in retrievals if item.content_state != "CAPTURED"]
    metrics = {
        "case_id": case.case_id, "query_count": len(query_rows), "query_budget": case.discovery_budget,
        "lead_count": len(all_leads), "unique_urls": len({item.url for item in all_leads}),
        "network_requests": network_requests, "acquisition_budget": case.acquisition_budget,
        "attempted_retrievals": len(retrievals),
        "successful_retrievals": sum(item.content_state == "CAPTURED" for item in retrievals),
        "blocked": sum(item.content_state == "BLOCKED" for item in retrievals),
        "failed": sum(item.content_state == "FAILED" for item in retrievals),
        "quarantined": sum(item.content_state == "QUARANTINED" for item in retrievals),
        "unique_content_hashes": len({item.content_hash for item in retrievals if item.content_hash}),
        "duplicate_bytes": sum(item.content_state == "CAPTURED" for item in retrievals) - len(sources),
        "media_types": {media: sum(item.media_type == media for item in retrievals)
                        for media in sorted({item.media_type for item in retrievals if item.media_type})},
        "languages": {language: sum(item.language == language and item.content_state == "CAPTURED" for item in retrievals)
                      for language in sorted(set(case.languages))},
        "parsed_documents": len(documents), "normalization_failures": len(sources) - len(documents),
        "stop_reason": run.stop_reason,
        "search_snippets_admitted_as_evidence": 0,
    }
    metrics["integrity_hash"] = sha256(metrics)
    write_json(root / "campaign_capture_metrics.json", metrics)
    write_json(root / "acquisition_failures.json", [item.to_record() for item in failures])
    return metrics


def renormalize_capture(capture_root: str | Path) -> dict[str, Any]:
    """Rebuild derivatives from immutable raw custody without network access."""
    root = Path(capture_root); custody = root / "custody"
    retrievals = {item["retrieval_id"]: RetrievalRecord(**item)
                  for item in read_jsonl(custody / "records" / "retrieval_records.jsonl")}
    sources = []
    for item in read_json(custody / "source_object_manifest.json"):
        payload = dict(item)
        for key in ("retrieval_ids", "requested_urls", "final_urls"): payload[key] = tuple(payload[key])
        sources.append(SourceRecord(**payload))
    documents = []
    for source in sources:
        documents.append(normalize_source(source, retrievals[source.retrieval_ids[0]],
                                          output_dir=custody / "normalized"))
    write_json(custody / "normalization_manifest.json", [item.to_record() for item in documents])
    result = {"documents": len(documents), "network_requests": 0,
              "gzip_derivatives": sum("CONTENT_ENCODING_GZIP_DECODED_FROM_IMMUTABLE_RAW_BYTES" in item.warnings
                                      for item in documents),
              "derivative_set_hash": sha256(sorted(item.derivative_hash for item in documents))}
    result["integrity_hash"] = sha256(result)
    write_json(custody / "renormalization_report.json", result)
    return result


def retry_captured_lead(*, case: InvestigationCase, capture_root: str | Path, url: str,
                        maximum_bytes: int, timeout_seconds: float = 60.0) -> dict[str, Any]:
    """Retry one preserved failure without changing its original custody record.

    This is for bounded technical failures such as an honestly enforced byte cap.  It
    cannot change the access decision, bypass a block, or exceed the case acquisition
    budget.  The retry relation is explicit and both attempts remain append-only.
    """
    root = Path(capture_root); discovery = root / "discovery"; custody = root / "custody"
    leads = [SearchLead(**item) for item in read_jsonl(discovery / "search_leads.jsonl")]
    lead = next((item for item in leads if item.url == url), None)
    if lead is None:
        raise ValueError("retry URL was not a captured search lead")
    access_values = [AccessDecision(**item) for item in read_jsonl(discovery / "access_decisions.jsonl")]
    decision = next((item for item in access_values if item.lead_id == lead.lead_id), None)
    if decision is None or decision.state != "ALLOW_PUBLIC_RETRIEVAL":
        raise PermissionError("retry cannot alter or bypass the original access decision")
    retrieval_values = [RetrievalRecord(**item) for item in read_jsonl(custody / "records" / "retrieval_records.jsonl")]
    prior = [item for item in retrieval_values if item.lead_id == lead.lead_id]
    completed_attempts = len(prior)
    original_count = len({item.lead_id for item in retrieval_values})
    if original_count + sum(max(0, len([value for value in retrieval_values if value.lead_id == item.lead_id]) - 1)
                            for item in leads) >= case.acquisition_budget:
        raise ValueError("acquisition budget exhausted")
    if not prior or prior[-1].content_state == "CAPTURED":
        raise ValueError("retry requires a preserved non-captured prior attempt")
    provider = read_json(discovery / "captured_provider_record.json")
    metadata = next(row for query in provider["queries"] for row in query.get("results", ())
                    if str(row["url"]) == url)
    retrieval, source = acquire_public_source(
        case_id=case.case_id, lead=lead, decision=decision, custody_root=custody,
        publisher=str(metadata.get("publisher", lead.apparent_source_authority)),
        source_class=str(metadata.get("source_class", "SECONDARY")), title=str(metadata.get("title", lead.title)),
        publication_time=metadata.get("publication_time"), timeout_seconds=timeout_seconds,
        maximum_bytes=maximum_bytes, retry_of=prior[-1].retrieval_id,
    )
    sources = []
    for item in read_json(custody / "source_object_manifest.json"):
        payload = dict(item)
        for key in ("retrieval_ids", "requested_urls", "final_urls"): payload[key] = tuple(payload[key])
        sources.append(SourceRecord(**payload))
    documents = list(read_json(custody / "normalization_manifest.json"))
    if source is not None:
        sources.append(source)
        document = normalize_source(source, retrieval, output_dir=custody / "normalized")
        documents.append(document.to_record())
    write_json(custody / "source_object_manifest.json", [item.to_record() for item in sources])
    write_json(custody / "normalization_manifest.json", documents)
    metrics = read_json(root / "campaign_capture_metrics.json")
    metrics["attempted_retrievals"] += 1; metrics["network_requests"] += 1
    if retrieval.content_state == "CAPTURED":
        metrics["successful_retrievals"] += 1; metrics["parsed_documents"] += 1
    else:
        key = retrieval.content_state.casefold()
        if key in metrics: metrics[key] += 1
    metrics["unique_content_hashes"] = len({item.content_hash for item in retrieval_values + [retrieval]
                                            if item.content_hash})
    metrics["languages"][retrieval.language] = metrics["languages"].get(retrieval.language, 0) + int(
        retrieval.content_state == "CAPTURED")
    metrics["integrity_hash"] = sha256({key: value for key, value in metrics.items() if key != "integrity_hash"})
    write_json(root / "campaign_capture_metrics.json", metrics)
    result = {"url": url, "prior_retrieval_id": prior[-1].retrieval_id,
              "retry_retrieval_id": retrieval.retrieval_id, "attempt_number": completed_attempts + 1,
              "content_state": retrieval.content_state, "failure": retrieval.failure,
              "byte_length": retrieval.byte_length, "source_object_id": source.source_object_id if source else None,
              "network_requests": 1, "access_decision_unchanged": True}
    result["integrity_hash"] = sha256(result)
    append_jsonl(root / "retry_report.jsonl", (result,))
    return result

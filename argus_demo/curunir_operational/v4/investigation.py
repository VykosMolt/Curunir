"""Investigation preregistration, planning, discovery lineage and access policy."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .models import (
    AccessDecision, CaseVersion, CoverageUpdate, DiscoveryQuery, DiscoveryRun,
    EvidenceRequirement, InvestigationCase, ResearchQuestion, SearchLead, StopRule,
    SubQuestion, canonical_json, sha256, stable_id,
)

CASE_VERSION = "curunir-investigation-case-v4"
PLANNER_VERSION = "curunir-deterministic-research-planner-v4"
ACCESS_POLICY_VERSION = "curunir-public-web-access-policy-v4"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seal(record: Mapping[str, Any]) -> str:
    return sha256({key: value for key, value in record.items() if key != "integrity_hash"})


def preregister_case(**values: Any) -> InvestigationCase:
    payload = {
        **values,
        "status": "PREREGISTERED",
        "current_phase": "PREREGISTERED",
        "version": 1,
        "integrity_hash": "0" * 64,
    }
    payload["integrity_hash"] = _seal(payload)
    return InvestigationCase(**payload)


def amend_case(case: InvestigationCase, *, changes: Mapping[str, Any], rationale: str,
               created_time: str | None = None) -> tuple[InvestigationCase, CaseVersion]:
    immutable = {"case_id", "research_question", "created_time", "integrity_hash"}
    if immutable & set(changes):
        raise ValueError("preregistered question and identity are immutable")
    if not changes or not rationale:
        raise ValueError("versioned amendment requires changes and rationale")
    payload = case.to_record(); payload.update(changes); payload["version"] = case.version + 1
    payload["integrity_hash"] = "0" * 64; payload["integrity_hash"] = _seal(payload)
    amended = InvestigationCase(**payload)
    stamp = created_time or now_utc()
    version_payload = {
        "version_id": stable_id("case-version", case.case_id, amended.version, payload["integrity_hash"]),
        "case_id": case.case_id, "version": amended.version,
        "parent_version_id": stable_id("case-version", case.case_id, case.version, case.integrity_hash),
        "changed_fields": tuple(sorted(changes)), "rationale": rationale, "created_time": stamp,
        "status": "AMENDED", "integrity_hash": "0" * 64,
    }
    version_payload["integrity_hash"] = _seal(version_payload)
    return amended, CaseVersion(**version_payload)


def transition_case(case: InvestigationCase, status: str, phase: str) -> InvestigationCase:
    payload = replace(case, status=status, current_phase=phase, integrity_hash="0" * 64).to_record()
    payload["integrity_hash"] = _seal(payload)
    return InvestigationCase(**payload)


def deterministic_plan(case: InvestigationCase, subquestions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    question = ResearchQuestion(stable_id("question", case.case_id, case.research_question), case.case_id,
                                case.research_question, case.purpose)
    subs: list[SubQuestion] = []; requirements: list[EvidenceRequirement] = []
    for index, row in enumerate(subquestions, 1):
        statement = str(row["statement"])
        source_classes = tuple(row.get("source_classes", case.source_classes))
        languages = tuple(row.get("languages", case.languages))
        sub = SubQuestion(stable_id("subquestion", question.question_id, index, statement),
                          question.question_id, statement, source_classes, languages,
                          str(row.get("contradiction_need", "Seek contrary or limiting evidence")))
        subs.append(sub)
        requirements.append(EvidenceRequirement(
            stable_id("evidence-requirement", case.case_id, index, statement), case.case_id,
            str(row.get("evidence_requirement", statement)), source_classes,
            int(row.get("minimum_sources", 1)), sub.contradiction_need,
        ))
    rules = tuple(StopRule(stable_id("stop-rule", case.case_id, index, value), case.case_id, value, index)
                  for index, value in enumerate(case.stop_rules, 1))
    return {
        "case_integrity_hash": case.integrity_hash,
        "planner": PLANNER_VERSION,
        "question": question.to_record(),
        "subquestions": [item.to_record() for item in subs],
        "evidence_requirements": [item.to_record() for item in requirements],
        "source_class_requirements": sorted(case.source_classes),
        "coverage_requirements": {"languages": list(case.languages), "geography": list(case.geographic_scope)},
        "exclusion_rules": list(case.exclusions),
        "prohibited_inferences": list(case.prohibited_inference_classes),
        "stop_rules": [item.to_record() for item in rules],
        "integrity_hash": sha256({"question": question.to_record(), "subquestions": [item.to_record() for item in subs],
                                  "requirements": [item.to_record() for item in requirements], "rules": [item.to_record() for item in rules]}),
    }


class DiscoveryBudget:
    def __init__(self, case: InvestigationCase):
        self.case = case; self.queries: list[DiscoveryQuery] = []; self.leads: dict[str, SearchLead] = {}

    def issue(self, *, subquestion_id: str, formulation: str, language: str, provider: str,
              provider_category: str, reason: str, expected_source_class: str,
              execution_time: str | None = None) -> DiscoveryQuery:
        if len(self.queries) >= self.case.discovery_budget:
            raise ValueError("discovery request budget exhausted")
        if language not in self.case.languages:
            raise ValueError("query language outside preregistration")
        stamp = execution_time or now_utc()
        query = DiscoveryQuery(stable_id("query", self.case.case_id, len(self.queries) + 1, formulation,
                                         language, provider), self.case.case_id,
                               stable_id("question", self.case.case_id, self.case.research_question),
                               subquestion_id, formulation, language, provider, provider_category,
                               reason, expected_source_class, stamp)
        self.queries.append(query); return query

    def admit_results(self, query: DiscoveryQuery, rows: Iterable[Mapping[str, Any]]) -> tuple[SearchLead, ...]:
        admitted: list[SearchLead] = []
        existing_urls = {lead.url for lead in self.leads.values()}
        for rank, row in enumerate(rows, 1):
            url = str(row["url"])
            lead = SearchLead(stable_id("lead", self.case.case_id, url), self.case.case_id,
                              str(row.get("title", "")), url, query.provider, rank,
                              str(row.get("snippet", "")), query.query_id, query.language,
                              query.execution_time, str(row.get("authority", "UNKNOWN")),
                              str(row.get("media_type", "UNKNOWN")), str(row.get("reason", query.reason)))
            if url not in existing_urls:
                self.leads[lead.lead_id] = lead; admitted.append(lead); existing_urls.add(url)
        novelty = len(admitted) / max(1, len(tuple(rows)) if isinstance(rows, tuple) else len(admitted))
        self.queries[-1] = replace(query, result_lead_ids=tuple(item.lead_id for item in admitted), novelty=novelty,
                                   follow_up_decision="CONTINUE" if novelty >= .2 else "LOW_NOVELTY_STOP_CANDIDATE")
        return tuple(admitted)

    def finish(self, stop_reason: str, completed_time: str | None = None) -> DiscoveryRun:
        if not stop_reason:
            raise ValueError("discovery requires an explicit stop reason")
        start = self.queries[0].execution_time if self.queries else now_utc()
        return DiscoveryRun(stable_id("discovery-run", self.case.case_id, len(self.queries), stop_reason),
                            self.case.case_id, tuple(item.query_id for item in self.queries), len(self.queries),
                            self.case.discovery_budget, start, completed_time or now_utc(), stop_reason)


def decide_access(case: InvestigationCase, lead: SearchLead, *, credential_required: bool = False,
                  access_controlled: bool = False, waf_or_captcha: bool = False,
                  legal_restriction: bool = False, out_of_scope: bool = False,
                  personal_data_risk: bool = False, metadata_only: bool = False,
                  unknown: bool = False, decided_time: str | None = None) -> AccessDecision:
    reasons: list[str] = []
    if credential_required: state = "DENY_CREDENTIAL_REQUIRED"; reasons.append("credential required")
    elif access_controlled: state = "DENY_ACCESS_CONTROLLED"; reasons.append("access control present")
    elif legal_restriction: state = "DENY_LEGAL_RESTRICTION"; reasons.append("legal restriction recorded")
    elif out_of_scope: state = "DENY_OUT_OF_SCOPE"; reasons.append("outside preregistered scope")
    elif personal_data_risk: state = "DENY_PERSONAL_DATA_RISK"; reasons.append("unnecessary personal data risk")
    elif waf_or_captcha: state = "BLOCKED_TECHNICALLY"; reasons.extend(("technical block", "no circumvention"))
    elif metadata_only: state = "ALLOW_METADATA_ONLY"; reasons.append("metadata sufficient")
    elif unknown: state = "UNKNOWN_REQUIRES_REVIEW"; reasons.append("access status unresolved")
    else:
        host = (urlparse(lead.url).hostname or "").casefold()
        if not host: state = "UNKNOWN_REQUIRES_REVIEW"; reasons.append("missing host")
        else: state = "ALLOW_PUBLIC_RETRIEVAL"; reasons.extend(("ordinary public web", "no credentials"))
    stamp = decided_time or now_utc()
    return AccessDecision(stable_id("access", case.case_id, lead.lead_id, state), case.case_id,
                          lead.lead_id, lead.url, state, tuple(reasons), ACCESS_POLICY_VERSION,
                          stamp, case.access_marking)


def coverage_update(case: InvestigationCase, requirement: EvidenceRequirement,
                    sources: Iterable[Mapping[str, Any]], novelty: float) -> CoverageUpdate:
    values = tuple(sources)
    return CoverageUpdate(stable_id("coverage", case.case_id, requirement.requirement_id, len(values)),
                          case.case_id, requirement.requirement_id,
                          tuple(str(row["source_object_id"]) for row in values),
                          tuple(sorted({str(row["source_class"]) for row in values})), novelty,
                          "SATISFIED" if len(values) >= requirement.minimum_sources else "OPEN")

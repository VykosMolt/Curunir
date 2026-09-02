"""Writing, checking, approving and exporting reports.

A report is a versioned structure over the analytical record, not generated
prose. Validation refuses a sentence that presents inference as observation,
stale or contested support as settled, historical state as current, a forecast
probability the forecast never carried, or independence a single source cannot
give. Approving is a human act on one fixed version, open dissent either
resolved or carried visibly, and a revision is a new version that leaves the
approved one in the log as it was.
"""
from __future__ import annotations

import html
import re
from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking, marking_from_record
from curunir_operational.canonical import parse_time, sha256

from .authority import authoritative_approval_state
from .contracts import (HUMAN_ONLY_DISPOSITIONS, ReportDisposition, ReportRecord,
                        ReportSection, ReportSentence)
from .errors import NotFound
from .projections import MissionProjection, REDACTED
from .provenance import claim_descent
from .store import WorkbenchStore

# The families a SUPPORTED sentence may rest on. Anything else is inference
# wearing a supported label.
_OBSERVATIONAL_BASIS = ("semantic_claim", "semantic_observation",
                        "fabric_manifestation")
_INFERENTIAL_STATES = ("INFERRED", "PREDICTED", "PLANNED")

_PROBABILITY_RE = re.compile(r"(?:(?<![\d.])(0?\.\d+)(?![\d%])|(\d{1,3})\s*%)")


class ReportConflict(Exception):
    """Another writer changed the report; the caller's view is stale."""


class ReportValidationError(Exception):
    def __init__(self, findings: list[dict]):
        super().__init__(f"{len(findings)} blocking validation findings")
        self.findings = findings


def _sentence(report_id: str, section_id: str, data: Mapping[str, Any]) -> ReportSentence:
    if "text" not in data or "status" not in data:
        raise ValueError("a sentence requires text and status")
    text = data["text"]
    return ReportSentence(
        sentence_id=data.get("sentence_id")
        or digest_id("sentence", report_id, section_id, text),
        text=text, status=data["status"],
        basis_refs=tuple(data.get("basis_refs", ())),
        assumption_ids=tuple(data.get("assumption_ids", ())),
        inference_note=data.get("inference_note", ""),
        unresolved_reason=data.get("unresolved_reason", ""),
        temporal_scope=data.get("temporal_scope", ""),
        asserts_independent=bool(data.get("asserts_independent", False)))


def _sections(report_id: str,
              sections: Iterable[Mapping[str, Any]]) -> tuple[ReportSection, ...]:
    built = []
    for section in sections:
        if "kind" not in section or "title" not in section:
            raise ValueError("a section requires kind and title")
        section_id = section.get("section_id") \
            or digest_id("section", report_id, section["kind"], section["title"])
        built.append(ReportSection(
            section_id=section_id, kind=section["kind"], title=section["title"],
            sentences=tuple(_sentence(report_id, section_id, s)
                            for s in section.get("sentences", ())),
            option_ids=tuple(section.get("option_ids", ()))))
    return tuple(built)


def create_report(store: WorkbenchStore, *, actor: str, marking: Marking,
                  now: str, title: str, question: str,
                  sections: Iterable[Mapping[str, Any]],
                  state_token: str) -> dict:
    report_id = digest_id("report", title, now)
    record = ReportRecord(
        report_id=report_id, version=1, title=title, question=question,
        author=actor, sections=_sections(report_id, sections), status="DRAFT",
        based_on_state_token=state_token, recorded_time=now, marking=marking)
    event = store.append(
        "WORKBENCH_REPORT_RECORDED", record,
        recorded_time=now, actor=actor)
    return event["record"]


def _current(store: WorkbenchStore, report_id: str) -> dict:
    record = store.current_reports().get(report_id)
    if record is None:
        raise NotFound(f"unknown report: {report_id}")
    return record


def _next_version(store: WorkbenchStore, current: Mapping[str, Any], *,
                  expected_version: int, actor: str, now: str, status: str,
                  change_note: str, sections: tuple[ReportSection, ...] | None = None,
                  title: str | None = None, question: str | None = None,
                  state_token: str | None = None,
                  content_author: str | None = None) -> dict:
    if current["version"] != expected_version:
        raise ReportConflict(
            f"report {current['report_id']} is at version {current['version']}, "
            f"you edited version {expected_version}")
    content_changed = any(value is not None for value in (
        sections, title, question, state_token,
    ))
    if content_changed and not content_author:
        raise ValueError(
            "content_author is required when report content or basis changes")
    kept_sections = sections if sections is not None else _sections(
        current["report_id"],
        [{**s, "sentences": list(s["sentences"])} for s in current["sections"]])
    record = ReportRecord(
        report_id=current["report_id"], version=current["version"] + 1,
        title=title if title is not None else current["title"],
        question=question if question is not None else current["question"],
        # An edit makes the editor the author; a status change keeps the
        # original one. Either way the event log names who acted.
        author=content_author if content_author is not None else current["author"],
        sections=kept_sections, status=status,
        based_on_state_token=state_token or current["based_on_state_token"],
        recorded_time=now,
        # A re-append never re-classifies; the record keeps its own marking.
        marking=marking_from_record(current["marking"]),
        change_note=change_note)
    try:
        event = store.append(
            "WORKBENCH_REPORT_RECORDED", record,
            recorded_time=now, actor=actor)
    except ValueError as error:
        # The store's version check caught another writer.
        raise ReportConflict(str(error)) from error
    return event["record"]


def edit_report(store: WorkbenchStore, report_id: str, *, actor: str,
                actor_kind: str = "HUMAN", marking: Marking,
                now: str, expected_version: int, sections: Iterable[Mapping[str, Any]],
                title: str | None = None, question: str | None = None,
                state_token: str | None = None, change_note: str = "") -> dict:
    if actor_kind != "HUMAN":
        raise PermissionError("report editing is a human act")
    current = _current(store, report_id)
    was_approved = current["status"] in ("APPROVED", "APPROVED_WITH_DISSENT")
    if was_approved:
        # Editing an approved report opens a new draft; the approved version
        # stays in the log and its supersession is recorded.
        change_note = change_note or f"revision of approved v{current['version']}"
    elif current["status"] in ("REJECTED", "WITHDRAWN"):
        change_note = (change_note + " " if change_note else "") + \
            f"[reopens {current['status']} v{current['version']}]"
    record = _next_version(store, current, expected_version=expected_version,
                           actor=actor, now=now, status="DRAFT",
                           change_note=change_note or "edit",
                           sections=_sections(report_id, sections), title=title,
                           question=question, state_token=state_token,
                           content_author=actor)
    if was_approved:
        _disposition(store, {"report_id": current["report_id"],
                             "version": current["version"]},
                     disposition="SUPERSEDED", actor=actor,
                     actor_kind=actor_kind, now=now,
                     note=f"superseded by draft v{record['version']}",
                     validation=None, state_token=state_token or "",
                     marking=marking_from_record(current["marking"]))
    return record


# ---- validation ----

def validate_report(projection: MissionProjection, report: Mapping[str, Any]) -> dict:
    """Check a report against the approver's own view; support they cannot see
    does not count."""
    findings: list[dict] = []

    def finding(code: str, sentence: Mapping[str, Any], detail: str, *,
                blocking: bool = True):
        findings.append({"code": code, "sentence_id": sentence["sentence_id"],
                         "text": sentence["text"][:120], "detail": detail,
                         "blocking": blocking})

    claim_states = {s["claim_id"]: s for s in projection.family("semantic_claim_state")}
    open_reviews = {r["subject_id"] for r in projection.family("review_item")
                    if r["status"] == "OPEN"}

    def resolve(ref: str) -> tuple[str, dict] | None:
        if ref == REDACTED:
            return None
        for family in ("semantic_claim", "semantic_observation", "fabric_manifestation",
                       "analytic_forecast", "analytic_assumption", "hypothesis",
                       "analytic_theme", "analytic_narrative", "impact_path",
                       "strategic_warning", "mission_objective", "response_option",
                       "stakeholder_assessment", "forecast_indicator"):
            record = projection.get(family, ref)
            if record is not None:
                return family, record
        return None

    for section in report["sections"]:
        for sentence in section["sentences"]:
            status = sentence["status"]
            refs = tuple(sentence.get("basis_refs", ()))
            if status == "SUPPORTED" and not refs:
                finding("NO_EVIDENCE_BASIS", sentence,
                        "a factual sentence carries no evidence basis")
                continue
            resolved: list[tuple[str, dict]] = []
            for ref in refs:
                hit = resolve(ref)
                if hit is None:
                    finding("UNRESOLVABLE_BASIS", sentence,
                            f"basis reference {ref!r} does not resolve in the "
                            "approving context")
                else:
                    resolved.append(hit)
            if status == "SUPPORTED":
                for family, record in resolved:
                    if family not in _OBSERVATIONAL_BASIS:
                        finding("INFERENCE_AS_OBSERVATION", sentence,
                                f"basis {family} is analytical inference; the "
                                "sentence must be EXPLICITLY_INFERENTIAL")
                    elif family == "semantic_claim" \
                            and record.get("epistemic_state") in _INFERENTIAL_STATES:
                        finding("INFERENCE_AS_OBSERVATION", sentence,
                                f"claim {record['claim_id']} is "
                                f"{record['epistemic_state']}; the sentence must "
                                "be EXPLICITLY_INFERENTIAL")
                # Contested support presented as settled.
                for family, record in resolved:
                    if family != "semantic_claim":
                        continue
                    claim_id = record["claim_id"]
                    state = claim_states.get(claim_id, {}).get("state", "ACTIVE")
                    if state in ("RETRACTED", "SUPERSEDED", "CORRECTED"):
                        finding("STALE_BASIS", sentence,
                                f"claim {claim_id} is {state}; the sentence "
                                "presents it as settled support")
                    if record.get("epistemic_state") == "DISPUTED" or claim_id in open_reviews:
                        finding("CONTESTED_AS_SETTLED", sentence,
                                f"claim {claim_id} is contested/under review; "
                                "the sentence renders it settled")
                # Historical support presented as current. This is read from
                # the support itself, so leaving temporal_scope empty is not a
                # way out of the check.
                if sentence.get("temporal_scope") != "HISTORICAL":
                    claims = [r for f, r in resolved if f == "semantic_claim"]
                    expired = [c for c in claims if c.get("valid_to")
                               and parse_time(c["valid_to"])
                               <= parse_time(projection.snapshot_time)]
                    if claims and len(expired) == len(claims):
                        finding("HISTORICAL_AS_CURRENT", sentence,
                                "every supporting claim's validity has ended; "
                                "the sentence presents historical state as current")
                    elif expired and not sentence.get("temporal_scope"):
                        finding("TEMPORAL_SCOPE_REQUIRED", sentence,
                                f"{len(expired)}/{len(claims)} supporting claims "
                                "have ended validity; state the sentence's "
                                "temporal scope (CURRENT or HISTORICAL) explicitly")
                    elif expired:
                        finding("PARTIALLY_HISTORICAL_BASIS", sentence,
                                f"{len(expired)}/{len(claims)} supporting claims "
                                "have ended validity", blocking=False)
                # Independence claimed over support that cannot give it.
                if sentence.get("asserts_independent"):
                    claims = [r for f, r in resolved if f == "semantic_claim"]
                    best = max((c.get("independent_basis_count", 0) for c in claims),
                               default=0)
                    if best < 2:
                        finding("INDEPENDENCE_MISREPRESENTED", sentence,
                                f"independent basis count is {best}; the sentence "
                                "asserts independent corroboration")
            # Every number a sentence quotes must match some authored version
            # of a forecast it references, taken together so that an honest
            # comparison passes. Probabilities written out in words are not
            # checked.
            sentence_forecasts = [r for f, r in resolved if f == "analytic_forecast"]
            if len(sentence_forecasts) > 1:
                finding("MULTI_FORECAST_NUMERIC_AMBIGUITY", sentence,
                        "the sentence references several forecasts; quoted "
                        "numbers are validated against the union of their "
                        "authored versions and cannot be bound to a specific "
                        "proposition", blocking=False)
            if sentence_forecasts:
                authored: set[float] = set()
                for record in sentence_forecasts:
                    authored |= {v["probability"] for v in
                                 projection.versions("analytic_forecast",
                                                     record["forecast_id"])}
                    authored.add(record["probability"])
                quoted = []
                for match in _PROBABILITY_RE.finditer(sentence["text"]):
                    if match.group(1):
                        quoted.append(float(match.group(1)))
                    else:
                        quoted.append(float(match.group(2)) / 100.0)
                bad = [q for q in quoted
                       if not any(abs(q - a) < 0.005 for a in authored)]
                if bad:
                    finding("FORECAST_PROBABILITY_MISMATCH", sentence,
                            f"sentence quotes {bad} but the referenced "
                            f"forecasts' authored versions are {sorted(authored)}")
    stale = report["based_on_state_token"] != projection.state_token
    dispositions = [projection.redact(d) for d in
                    projection.store.report_dispositions(report["report_id"])
                    if projection.get("workbench_report_disposition",
                                      d["disposition_id"]) is not None]
    return {"report_id": report["report_id"], "version": report["version"],
            "open_dissent": open_dissent(projection, report["report_id"]),
            "prior_dispositions": [{"disposition": d["disposition"],
                                    "report_version": d["report_version"],
                                    "actor_id": d["actor_id"],
                                    "note": d["note"]} for d in dispositions],
            "validation_note": "word-form probabilities are not machine-checked; "
                               "numeric probabilities are validated against "
                               "authored forecast versions",
            "state_token": projection.state_token,
            "based_on_state_token": report["based_on_state_token"],
            "stale_basis_state": stale,
            "findings": findings,
            "blocking": [f for f in findings if f["blocking"]],
            "ok": not any(f["blocking"] for f in findings)}


# ---- workflow ----

def _disposition(store: WorkbenchStore, report: Mapping[str, Any], *,
                 disposition: str, actor: str, actor_kind: str, now: str,
                 note: str, validation: Mapping[str, Any] | None,
                 state_token: str, marking: Marking,
                 dissent_ids: tuple[str, ...] = ()) -> dict:
    record = ReportDisposition(
        disposition_id=digest_id("disposition", report["report_id"],
                                 str(report["version"]), disposition, now),
        report_id=report["report_id"], report_version=report["version"],
        disposition=disposition, actor_id=actor, actor_kind=actor_kind,
        note=note, validation_sha256=sha256(validation) if validation else "",
        state_token=state_token, dissent_annotation_ids=dissent_ids,
        recorded_time=now, marking=marking)
    event = store.append(
        "WORKBENCH_REPORT_DISPOSITION_RECORDED", record,
        recorded_time=now, actor=actor)
    return event["record"]


def submit_report(store: WorkbenchStore, report_id: str, *, actor: str,
                  actor_kind: str = "HUMAN", marking: Marking, now: str,
                  expected_version: int, state_token: str) -> dict:
    current = _current(store, report_id)
    if current["status"] not in ("DRAFT", "RETURNED_FOR_REVISION"):
        raise ValueError(f"cannot submit a report in status {current['status']}")
    record = _next_version(store, current, expected_version=expected_version,
                           actor=actor, now=now, status="IN_REVIEW",
                           change_note="submitted for review")
    _disposition(store, record, disposition="SUBMITTED", actor=actor,
                 actor_kind=actor_kind, now=now, note="", validation=None,
                 state_token=state_token,
                 marking=marking_from_record(record["marking"]))
    return record


def open_dissent(projection: MissionProjection, report_id: str) -> list[dict]:
    """Open dissent on the report or any of its sections and sentences, in every
    version, so dissent cannot slip the approval gate by being anchored deep or
    by having its sentence reworded."""
    part_ids = {report_id}
    for version in projection.versions("workbench_report", report_id):
        for section in version["sections"]:
            part_ids.add(section["section_id"])
            part_ids |= {x["sentence_id"] for x in section["sentences"]}
    return [a for a in projection.family("workbench_annotation")
            if a["kind"] == "DISSENT" and a["status"] == "OPEN"
            and (a["target_id"] in part_ids or a.get("anchor_ref") in part_ids)]


def approve_report(store: WorkbenchStore, projection: MissionProjection,
                   report_id: str, *, actor: str, actor_kind: str,
                   marking: Marking, now: str, expected_version: int,
                   note: str = "", acknowledge_dissent: tuple[str, ...] = ()) -> dict:
    """Approve a report: human only, after validation, with dissent accounted for.

    The projection must be the approver's own view, so support they cannot see
    blocks the approval rather than quietly counting towards it."""
    if actor_kind != "HUMAN":
        raise PermissionError("report approval is a human act")
    current = _current(store, report_id)
    if current["status"] != "IN_REVIEW":
        raise ValueError(f"cannot approve a report in status {current['status']}")
    authority = authoritative_approval_state(
        store,
        current,
        context=projection.context,
        actor=actor,
        acknowledged_dissent=acknowledge_dissent,
    )
    if authority["separation_of_duties_violation"]:
        raise PermissionError(
            "separation of duties: an analyst who authored or submitted this "
            "report cannot also approve it")
    validation = validate_report(projection, current)
    if not validation["ok"]:
        raise ReportValidationError(validation["blocking"])
    if authority["unresolved_basis"]:
        raise ValueError(
            "authoritative report basis is unresolved; approval refused")
    if authority["blocking_basis_concerns"]:
        anchor = next((sentence for section in current["sections"]
                       for sentence in section.get("sentences", ())), {
                           "sentence_id": current["report_id"],
                           "text": current["title"],
                       })
        raise ReportValidationError([{
            "code": concern["code"],
            "sentence_id": anchor["sentence_id"],
            "text": anchor["text"][:120],
            "detail": concern["reason"],
            "blocking": True,
        } for concern in authority["blocking_basis_concerns"]])
    if authority["hidden_basis_concerns"]:
        raise ValueError(
            "the report basis has a current state or open review the approver "
            "is not cleared to view; approval requires a cleared reviewer")
    if authority["hidden_dissent_ids"]:
        raise ValueError(
            "open dissent exists that the approver is not cleared to view; it "
            "must be resolved by a cleared reviewer")
    dissent_ids = tuple(authority["visible_dissent_ids"])
    if authority["unacknowledged_dissent_ids"]:
        raise ValueError(
            "open dissent exists on this report; approve with "
            f"acknowledge_dissent={authority['unacknowledged_dissent_ids']} "
            "to carry it visibly, "
            "or resolve it first")
    disposition = "APPROVED_WITH_DISSENT" if dissent_ids else "APPROVED"
    record = _next_version(store, current, expected_version=expected_version,
                           actor=actor, now=now, status=disposition,
                           change_note=note or "approved")
    _disposition(store, record, disposition=disposition, actor=actor,
                 actor_kind=actor_kind, now=now, note=note,
                 validation=validation, state_token=projection.state_token,
                 marking=marking_from_record(record["marking"]),
                 dissent_ids=dissent_ids)
    return record


def reject_report(store: WorkbenchStore, report_id: str, *, actor: str,
                  actor_kind: str, marking: Marking, now: str,
                  expected_version: int, note: str,
                  return_for_revision: bool = False,
                  state_token: str = "") -> dict:
    if actor_kind != "HUMAN":
        raise PermissionError("report disposition is a human act")
    current = _current(store, report_id)
    if current["status"] != "IN_REVIEW":
        raise ValueError(f"cannot disposition a report in status {current['status']}")
    status = "RETURNED_FOR_REVISION" if return_for_revision else "REJECTED"
    record = _next_version(store, current, expected_version=expected_version,
                           actor=actor, now=now, status=status,
                           change_note=note)
    _disposition(store, record, disposition=status, actor=actor,
                 actor_kind=actor_kind, now=now, note=note, validation=None,
                 state_token=state_token,
                 marking=marking_from_record(record["marking"]))
    return record


# ---- role projections ----

_EXECUTIVE_KINDS = ("executive_summary", "key_judgments", "warnings", "forecasts",
                    "decision_options", "unresolved_risks", "information_gaps",
                    "dissent")
_OPERATOR_KINDS = ("decision_options", "collection_status", "information_gaps",
                   "unresolved_risks")


def role_view(projection: MissionProjection, report: Mapping[str, Any],
              role: str) -> dict[str, Any]:
    """One report seen through one role's lens; never a separate summary, and
    every sentence keeps its status label."""
    base = {"report_id": report["report_id"], "version": report["version"],
            "title": report["title"], "question": report["question"],
            "status": report["status"], "author": report["author"],
            "role": role, "based_on_state_token": report["based_on_state_token"]}
    if role == "ANALYST":
        return {**base, "sections": report["sections"],
                "validation": validate_report(projection, report),
                "dissent": [a for a in projection.annotations_for(report["report_id"])
                            if a["kind"] == "DISSENT"]}
    if role == "EXECUTIVE":
        sections = [s for s in report["sections"] if s["kind"] in _EXECUTIVE_KINDS]
        return {**base, "sections": sections,
                "uncertainty_note": _uncertainty_note(report)}
    if role == "SOURCE_LEGAL":
        sections = []
        for section in report["sections"]:
            sentences = []
            for sentence in section["sentences"]:
                lineage = []
                for ref in sentence.get("basis_refs", ()):
                    descent = claim_descent(projection, ref)
                    if descent is not None:
                        lineage.append(descent)
                    else:
                        record = projection.get("semantic_observation", ref) \
                            or projection.get("fabric_manifestation", ref)
                        lineage.append({"ref": ref, "resolved": record is not None,
                                        "record": record})
                sentences.append({**sentence, "lineage": lineage})
            sections.append({**section, "sentences": sentences})
        return {**base, "sections": sections}
    if role == "OPERATOR":
        sections = [s for s in report["sections"] if s["kind"] in _OPERATOR_KINDS]
        options = []
        for section in report["sections"]:
            for option_id in section.get("option_ids", ()):
                record = projection.get("response_option", option_id)
                if record is not None:
                    options.append(record)
        open_tasks = [t for t in projection.base_view["analyst_tasks"]
                      if t["status"] in ("ASSIGNED", "IN_PROGRESS", "BLOCKED")]
        return {**base, "sections": sections, "decision_options": options,
                "open_tasks": open_tasks}
    raise ValueError(f"unknown report role: {role}")


def _uncertainty_note(report: Mapping[str, Any]) -> dict[str, int]:
    counts = {"SUPPORTED": 0, "EXPLICITLY_INFERENTIAL": 0, "UNRESOLVED": 0}
    for section in report["sections"]:
        for sentence in section["sentences"]:
            counts[sentence["status"]] += 1
    return counts


# ---- export ----

def export_package(projection: MissionProjection, store: WorkbenchStore,
                   report_id: str) -> dict[str, Any] | None:
    """Everything a receiver needs to check the report's lineage: its versions,
    dispositions and expanded basis, limited to what the exporter can see."""
    current = projection.get("workbench_report", report_id)
    if current is None:
        return None
    versions = projection.versions("workbench_report", report_id)
    dispositions = [projection.redact(d) for d in store.report_dispositions(report_id)
                    if projection.get("workbench_report_disposition", d["disposition_id"]) is not None]
    basis: dict[str, Any] = {}
    for section in current["sections"]:
        for sentence in section["sentences"]:
            for ref in sentence.get("basis_refs", ()):
                if ref in basis or ref == REDACTED:
                    continue
                basis[ref] = claim_descent(projection, ref) \
                    or projection.get("semantic_observation", ref) \
                    or projection.get("fabric_manifestation", ref) \
                    or projection.get("analytic_forecast", ref) \
                    or projection.get("analytic_assumption", ref)
    part_ids = {report_id}
    for version in versions:
        for section in version["sections"]:
            part_ids.add(section["section_id"])
            part_ids |= {x["sentence_id"] for x in section["sentences"]}
    annotations = [a for a in projection.family("workbench_annotation")
                   if a["target_id"] in part_ids
                   or a.get("anchor_ref") in part_ids]
    return {"format": "curunir-workbench-report-export-v1",
            "state_token": projection.state_token,
            "report": current, "versions": versions,
            "dispositions": dispositions, "basis": basis,
            "annotations": annotations,
            "dissent": [a for a in annotations if a["kind"] == "DISSENT"]}


def export_html(projection: MissionProjection, report: Mapping[str, Any]) -> str:
    """A standalone HTML report where every sentence shows its status, so
    nothing reads as bare fact."""
    parts = [f"<h1>{html.escape(report['title'])}</h1>",
             f"<p class='meta'>version {report['version']} · {html.escape(report['status'])} · "
             f"author {html.escape(report['author'])} · "
             f"state {html.escape(report['based_on_state_token'])}</p>",
             f"<p class='question'>{html.escape(report['question'])}</p>"]
    for section in report["sections"]:
        parts.append(f"<h2>{html.escape(section['title'])}</h2>")
        for sentence in section["sentences"]:
            refs = ", ".join(sentence.get("basis_refs", ())[:6])
            status = sentence["status"]
            note = ""
            if status == "EXPLICITLY_INFERENTIAL":
                note = f" — inference: {html.escape(sentence.get('inference_note', ''))}"
            if status == "UNRESOLVED":
                note = f" — unresolved: {html.escape(sentence.get('unresolved_reason', ''))}"
            parts.append(
                f"<p class='sentence s-{status.lower()}'>{html.escape(sentence['text'])} "
                f"<span class='tag'>[{status}]</span>"
                f"<span class='refs'>{html.escape(refs)}{note}</span></p>")
    part_ids = {report["report_id"]}
    for section in report["sections"]:
        part_ids.add(section["section_id"])
        part_ids |= {x["sentence_id"] for x in section["sentences"]}
    dissent = [a for a in projection.family("workbench_annotation")
               if a["kind"] == "DISSENT"
               and (a["target_id"] in part_ids or a.get("anchor_ref") in part_ids)]
    if report["status"] == "APPROVED_WITH_DISSENT" or dissent:
        parts.append("<h2>Dissent</h2>")
        if not dissent:
            parts.append("<p class='meta'>approved with dissent recorded on a "
                         "prior version — see the export package</p>")
        for a in dissent:
            parts.append(f"<p class='sentence s-unresolved'>"
                         f"{html.escape(a['author'])}: {html.escape(a['text'])} "
                         f"<span class='tag'>[DISSENT/{html.escape(a['status'])}]</span></p>")
    body = "\n".join(parts)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{report['report_id']}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem}}
.meta,.refs{{color:#555;font-size:.8rem;display:block}}
.tag{{font-size:.7rem;font-weight:700;border:1px solid #888;padding:0 .3rem;margin-left:.4rem}}
.s-unresolved{{border-left:3px solid #b80;padding-left:.5rem}}
.s-explicitly_inferential{{border-left:3px solid #46a;padding-left:.5rem}}
</style></head><body>{body}</body></html>"""

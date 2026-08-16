"""Report / decision-dossier engine.

A report is a versioned structured projection of the analytical record, not
generated prose. The invariants:

  * every sentence is SUPPORTED, EXPLICITLY_INFERENTIAL or UNRESOLVED;
  * SUPPORTED requires basis references that resolve — for the approving
    actor — to observation-backed propositions; inference laundered through
    a SUPPORTED label is a validation rejection, not a style issue;
  * a forecast probability quoted in a sentence must equal the actual
    authored forecast record;
  * historical state rendered as current, contested state rendered as
    settled, and independence asserted over a single origin family are all
    rejections;
  * approval is a recorded human disposition over one immutable version;
    open dissent blocks plain approval — it is either resolved or carried
    visibly as APPROVED_WITH_DISSENT;
  * later revision is a new version; the approved version stays in the log
    exactly as approved.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking
from curunir_operational.canonical import parse_time, sha256

from curunir_operational.access import marking_from_record

from .contracts import (HUMAN_ONLY_DISPOSITIONS, ReportDisposition, ReportRecord,
                        ReportSection, ReportSentence)
from .errors import NotFound
from .projections import MissionProjection, REDACTED
from .store import WorkbenchStore

# basis families a SUPPORTED sentence may rest on (observation-backed record
# families); anything else is inference wearing a supported label
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


def _sentence(section_id: str, data: Mapping[str, Any]) -> ReportSentence:
    if "text" not in data or "status" not in data:
        raise ValueError("a sentence requires text and status")
    text = data["text"]
    return ReportSentence(
        sentence_id=data.get("sentence_id") or digest_id("sentence", section_id, text),
        text=text, status=data["status"],
        basis_refs=tuple(data.get("basis_refs", ())),
        assumption_ids=tuple(data.get("assumption_ids", ())),
        inference_note=data.get("inference_note", ""),
        unresolved_reason=data.get("unresolved_reason", ""),
        temporal_scope=data.get("temporal_scope", ""),
        asserts_independent=bool(data.get("asserts_independent", False)))


def _sections(sections: Iterable[Mapping[str, Any]]) -> tuple[ReportSection, ...]:
    built = []
    for section in sections:
        if "kind" not in section or "title" not in section:
            raise ValueError("a section requires kind and title")
        section_id = section.get("section_id") or digest_id("section", section["kind"], section["title"])
        built.append(ReportSection(
            section_id=section_id, kind=section["kind"], title=section["title"],
            sentences=tuple(_sentence(section_id, s) for s in section.get("sentences", ())),
            option_ids=tuple(section.get("option_ids", ()))))
    return tuple(built)


def create_report(store: WorkbenchStore, *, actor: str, marking: Marking,
                  now: str, title: str, question: str,
                  sections: Iterable[Mapping[str, Any]],
                  state_token: str) -> dict:
    report_id = digest_id("report", title, now)
    record = ReportRecord(
        report_id=report_id, version=1, title=title, question=question,
        author=actor, sections=_sections(sections), status="DRAFT",
        based_on_state_token=state_token, recorded_time=now, marking=marking)
    store.append("WORKBENCH_REPORT_RECORDED", record, recorded_time=now, actor=actor)
    return record.to_record()


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
    kept_sections = sections if sections is not None else _sections(
        [{**s, "sentences": list(s["sentences"])} for s in current["sections"]])
    record = ReportRecord(
        report_id=current["report_id"], version=current["version"] + 1,
        title=title if title is not None else current["title"],
        question=question if question is not None else current["question"],
        # content edits carry the editing analyst as author; status
        # transitions keep the drafting author — the acting party is on the
        # event log and the disposition
        author=content_author if content_author is not None else current["author"],
        sections=kept_sections, status=status,
        based_on_state_token=state_token or current["based_on_state_token"],
        recorded_time=now,
        # a re-append NEVER re-classifies: the record keeps its own marking
        marking=marking_from_record(current["marking"]),
        change_note=change_note)
    try:
        store.append("WORKBENCH_REPORT_RECORDED", record, recorded_time=now, actor=actor)
    except ValueError as error:
        # the store's strict next-version enforcement caught a concurrent writer
        raise ReportConflict(str(error)) from error
    return record.to_record()


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
        # editing an approved output opens a NEW draft version; the approved
        # version stays immutable history and its supersession is recorded
        change_note = change_note or f"revision of approved v{current['version']}"
    elif current["status"] in ("REJECTED", "WITHDRAWN"):
        change_note = (change_note + " " if change_note else "") + \
            f"[reopens {current['status']} v{current['version']}]"
    record = _next_version(store, current, expected_version=expected_version,
                           actor=actor, now=now, status="DRAFT",
                           change_note=change_note or "edit",
                           sections=_sections(sections), title=title,
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


# ---- validation --------------------------------------------------------------

def validate_report(projection: MissionProjection, report: Mapping[str, Any]) -> dict:
    """Structural validation against the approving actor's own authorized
    projection: support that the approver cannot see does not count."""
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
                # contested/disputed basis rendered as settled
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
                # historical rendered as current — derived from the BASIS,
                # not from an author-supplied flag: omitting temporal_scope
                # does not opt out
                if sentence.get("temporal_scope") != "HISTORICAL":
                    claims = [r for f, r in resolved if f == "semantic_claim"]
                    expired = [c for c in claims if c.get("valid_to")
                               and parse_time(c["valid_to"])
                               <= parse_time(projection.snapshot_time)]
                    if claims and len(expired) == len(claims):
                        finding("HISTORICAL_AS_CURRENT", sentence,
                                "every supporting claim's validity has ended; "
                                "the sentence presents historical state as current")
                    elif expired:
                        finding("PARTIALLY_HISTORICAL_BASIS", sentence,
                                f"{len(expired)}/{len(claims)} supporting claims "
                                "have ended validity; mark the sentence HISTORICAL "
                                "or narrow the basis", blocking=False)
                # independence honesty
                if sentence.get("asserts_independent"):
                    claims = [r for f, r in resolved if f == "semantic_claim"]
                    best = max((c.get("independent_basis_count", 0) for c in claims),
                               default=0)
                    if best < 2:
                        finding("INDEPENDENCE_MISREPRESENTED", sentence,
                                f"independent basis count is {best}; the sentence "
                                "asserts independent corroboration")
            # forecast probability fidelity (any status): EVERY quoted
            # numeric probability must equal some AUTHORED version of the
            # referenced forecast — word-form probabilities ("three in
            # four") are a stated limitation, not silently accepted
            for family, record in resolved:
                if family != "analytic_forecast":
                    continue
                authored = {v["probability"] for v in
                            projection.versions("analytic_forecast",
                                                record["forecast_id"])} \
                    | {record["probability"]}
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
                            f"sentence quotes {bad} but forecast "
                            f"{record['forecast_id']}'s authored versions are "
                            f"{sorted(authored)}")
    stale = report["based_on_state_token"] != projection.state_token
    dispositions = [projection.redact(d) for d in
                    projection.store.report_dispositions(report["report_id"])
                    if projection.get("workbench_report_disposition",
                                      d["disposition_id"]) is not None]
    return {"report_id": report["report_id"], "version": report["version"],
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


# ---- workflow ----------------------------------------------------------------

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
    store.append("WORKBENCH_REPORT_DISPOSITION_RECORDED", record,
                 recorded_time=now, actor=actor)
    return record.to_record()


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
    """Open dissent on the report OR any of its sentences/sections — dissent
    anchored below report level must not dodge the approval gate."""
    report = projection.get("workbench_report", report_id)
    part_ids = {report_id}
    if report is not None:
        for section in report["sections"]:
            part_ids.add(section["section_id"])
            part_ids |= {x["sentence_id"] for x in section["sentences"]}
    return [a for a in projection.family("workbench_annotation")
            if a["kind"] == "DISSENT" and a["status"] == "OPEN"
            and (a["target_id"] in part_ids or a.get("anchor_ref") in part_ids)]


def approve_report(store: WorkbenchStore, projection: MissionProjection,
                   report_id: str, *, actor: str, actor_kind: str,
                   marking: Marking, now: str, expected_version: int,
                   note: str = "", acknowledge_dissent: tuple[str, ...] = ()) -> dict:
    """Approval: human-only, validation-gated, dissent-aware.

    `projection` must be the approving actor's own authorized view — support
    the approver cannot see blocks approval instead of silently counting."""
    if actor_kind != "HUMAN":
        raise PermissionError("report approval is a human act")
    current = _current(store, report_id)
    if current["status"] != "IN_REVIEW":
        raise ValueError(f"cannot approve a report in status {current['status']}")
    validation = validate_report(projection, current)
    if not validation["ok"]:
        raise ReportValidationError(validation["blocking"])
    dissent = open_dissent(projection, report_id)
    dissent_ids = tuple(a["annotation_id"] for a in dissent)
    if dissent and set(dissent_ids) - set(acknowledge_dissent):
        raise ValueError(
            "open dissent exists on this report; approve with "
            f"acknowledge_dissent={sorted(dissent_ids)} to carry it visibly, "
            "or resolve it first")
    disposition = "APPROVED_WITH_DISSENT" if dissent else "APPROVED"
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


# ---- role projections --------------------------------------------------------

_EXECUTIVE_KINDS = ("executive_summary", "key_judgments", "warnings", "forecasts",
                    "decision_options", "unresolved_risks", "information_gaps",
                    "dissent")
_OPERATOR_KINDS = ("decision_options", "collection_status", "information_gaps",
                   "unresolved_risks")


def role_view(projection: MissionProjection, report: Mapping[str, Any],
              role: str) -> dict[str, Any]:
    """Role-specific transformations of ONE report state — never independent
    summaries. Sentence status labels survive every projection."""
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
                    from .provenance import claim_descent
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


# ---- export ------------------------------------------------------------------

def export_package(projection: MissionProjection, store: WorkbenchStore,
                   report_id: str) -> dict[str, Any] | None:
    """Structured machine-readable export: report versions, dispositions,
    expanded basis references — everything the receiving side needs to check
    lineage, restricted to the exporting context's authorized view."""
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
                from .provenance import claim_descent
                basis[ref] = claim_descent(projection, ref) \
                    or projection.get("semantic_observation", ref) \
                    or projection.get("fabric_manifestation", ref) \
                    or projection.get("analytic_forecast", ref) \
                    or projection.get("analytic_assumption", ref)
    return {"format": "curunir-workbench-report-export-v1",
            "state_token": projection.state_token,
            "report": current, "versions": versions,
            "dispositions": dispositions, "basis": basis,
            "annotations": projection.annotations_for(report_id)}


def export_html(projection: MissionProjection, report: Mapping[str, Any]) -> str:
    """Deterministic self-contained human-readable report. Every sentence
    shows its epistemic status; nothing renders as bare unqualified fact."""
    import html as _html
    parts = [f"<h1>{_html.escape(report['title'])}</h1>",
             f"<p class='meta'>version {report['version']} · {_html.escape(report['status'])} · "
             f"author {_html.escape(report['author'])} · "
             f"state {_html.escape(report['based_on_state_token'])}</p>",
             f"<p class='question'>{_html.escape(report['question'])}</p>"]
    for section in report["sections"]:
        parts.append(f"<h2>{_html.escape(section['title'])}</h2>")
        for sentence in section["sentences"]:
            refs = ", ".join(sentence.get("basis_refs", ())[:6])
            status = sentence["status"]
            note = ""
            if status == "EXPLICITLY_INFERENTIAL":
                note = f" — inference: {_html.escape(sentence.get('inference_note', ''))}"
            if status == "UNRESOLVED":
                note = f" — unresolved: {_html.escape(sentence.get('unresolved_reason', ''))}"
            parts.append(
                f"<p class='sentence s-{status.lower()}'>{_html.escape(sentence['text'])} "
                f"<span class='tag'>[{status}]</span>"
                f"<span class='refs'>{_html.escape(refs)}{note}</span></p>")
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

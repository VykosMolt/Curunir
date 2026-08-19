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
from curunir_operational.store import StoreError

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
    # a CONTENT change (new sections) must name its content_author, or the version
    # would keep the prior drafter's `author` while someone else rewrote it —
    # decoupling content from attribution and eroding separation of duties
    # (review finding 6). Status transitions (sections is None) legitimately keep
    # the drafting author.
    if sections is not None and content_author is None:
        raise ValueError(
            "a content change must name its content_author (separation-of-duties)")
    kept_sections = sections if sections is not None else _sections(
        current["report_id"],
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
    except StoreError as error:
        # the store's strict next-version enforcement caught a concurrent writer.
        # ONLY a StoreError is a conflict: a malformed-content ValueError (a
        # non-finite float / lone surrogate refused by canonical_line) must stay a
        # 400, not be relabelled a version conflict (review MINOR-1)
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

    from curunir_analytic.contracts import SETTLED_SUPPORT_STATUSES
    from curunir_semantic.contracts import HYPOTHESIS_SETTLED_SUPPORT_STATUSES
    _SETTLED = {
        **SETTLED_SUPPORT_STATUSES,
        "hypothesis": HYPOTHESIS_SETTLED_SUPPORT_STATUSES,
    }

    def _dead(family: str, status: str) -> bool:
        if not status:
            return False
        live = _SETTLED.get(family)
        if live is None:
            return False
        return status not in live

    for section in report["sections"]:
        # option_ids are report-level decision options — not only live
        # when a SUPPORTED/INFERENTIAL sentence happens to sit nearby
        # (review R34A-4)
        sentences = section["sentences"]
        anchor = sentences[0] if sentences else {
            "sentence_id": section.get("section_id") or "section",
            "text": section.get("title") or "section",
        }
        for option_id in section.get("option_ids") or ():
            rec = projection.get("response_option", option_id)
            if rec is None:
                store = getattr(projection, "store", None)
                if store is not None and hasattr(store, "current_analytics"):
                    rec = store.current_analytics("response_option").get(option_id)
            if rec is not None and _dead("response_option", rec.get("status") or ""):
                finding("CONTESTED_AS_SETTLED", anchor,
                        f"response_option {rec.get('status')} is not live "
                        "support; the report presents it as a decision option")
        store = getattr(projection, "store", None)
        option_set = {i for i in (section.get("option_ids") or ()) if i}
        for sentence in sentences:
            option_set |= {i for i in (sentence.get("basis_refs") or ()) if i}
            option_set |= {i for i in (sentence.get("assumption_ids") or ()) if i}
        if store is not None and option_set:
            walked_opts: set = set()
            related_opts = _related_claim_ids(store, option_set, walked_opts)
            for claim_id in related_opts:
                state = claim_states.get(claim_id, {}).get("state", "ACTIVE")
                if state in ("RETRACTED", "SUPERSEDED", "CORRECTED",
                             "STALE", "DISPUTED", "SOURCE_WITHDRAWN"):
                    finding("STALE_BASIS", anchor,
                            f"claim {claim_id} is {state}; a decision option "
                            "presents it as settled support")
            extra_opts = walked_opts | option_set
            for wid in extra_opts:
                rec = None
                fam = ""
                if hasattr(store, "current_analytics"):
                    from curunir_analytic.store import ANALYTIC_ID_FIELDS
                    for kind in ANALYTIC_ID_FIELDS:
                        rec = store.current_analytics(kind).get(wid)
                        if rec is not None:
                            fam = kind
                            break
                if rec is None and hasattr(store, "current_hypotheses"):
                    rec = store.current_hypotheses().get(wid)
                    fam = "hypothesis"
                if rec is not None and _dead(fam, rec.get("status") or ""):
                    finding("CONTESTED_AS_SETTLED", anchor,
                            f"{fam} {rec.get('status')} is not live "
                            "support; a decision option presents it settled")
            subjects = extra_opts | related_opts
            for item in projection.family("review_item"):
                if item.get("status") == "OPEN" \
                        and item.get("subject_id") in subjects:
                    finding("CONTESTED_AS_SETTLED", anchor,
                            f"{item.get('subject_kind')} "
                            f"{item.get('subject_id')} has open review; "
                            "a decision option presents it settled")
            live_claims = []
            for claim_id in related_opts:
                rec = projection.get("semantic_claim", claim_id)
                if rec is None:
                    continue
                live_claims.append(rec)
                if rec.get("epistemic_state") == "DISPUTED":
                    finding("CONTESTED_AS_SETTLED", anchor,
                            f"claim {claim_id} is contested; a cited object "
                            "presents it as settled support")
            expired = [c for c in live_claims if c.get("valid_to")
                       and parse_time(c["valid_to"])
                       <= parse_time(projection.snapshot_time)]
            if live_claims and len(expired) == len(live_claims):
                finding("HISTORICAL_AS_CURRENT", anchor,
                        "every supporting claim's validity has ended; "
                        "the report presents it as current")
        for sentence in sentences:
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
            if status in ("SUPPORTED", "EXPLICITLY_INFERENTIAL"):
                # visible retractions/contests on related claims — including
                # inferential citations (the legal way to cite a forecast).
                # Hidden retractions remain Ck2 (review R28A-1 / R27A-4).
                related_for_sentence: set = {
                    rec.get("claim_id") for fam, rec in resolved
                    if fam == "semantic_claim" and rec.get("claim_id")
                }
                store = getattr(projection, "store", None)
                walked: set = set()
                cite = {i for i in (*sentence.get("basis_refs", ()),
                                    *sentence.get("assumption_ids", ()),
                                    *section.get("option_ids", ()))
                        if i}
                if store is not None:
                    related_for_sentence |= _related_claim_ids(store, cite, walked)
                for claim_id in related_for_sentence:
                    state = claim_states.get(claim_id, {}).get("state", "ACTIVE")
                    if state in ("RETRACTED", "SUPERSEDED", "CORRECTED",
                                 "STALE", "DISPUTED", "SOURCE_WITHDRAWN"):
                        finding("STALE_BASIS", sentence,
                                f"claim {claim_id} is {state}; the sentence "
                                "presents it as settled support")
                    claim_rec = next((r for f, r in resolved
                                      if f == "semantic_claim"
                                      and r.get("claim_id") == claim_id), None)
                    if (claim_rec or {}).get("epistemic_state") == "DISPUTED" \
                            or claim_id in open_reviews:
                        finding("CONTESTED_AS_SETTLED", sentence,
                                f"claim {claim_id} is contested/under review; "
                                "the sentence renders it settled")
                # visible OPEN reviews on cited/walked analytic subjects
                # (forecast COVERAGE_GAP, hypothesis STALE_BASIS) — Ck2 only
                # sees the hidden half (review R29A C-1)
                subjects = cite | walked | related_for_sentence
                for item in projection.family("review_item"):
                    if item.get("status") == "OPEN" \
                            and item.get("subject_id") in subjects:
                        finding("CONTESTED_AS_SETTLED", sentence,
                                f"{item.get('subject_kind')} "
                                f"{item.get('subject_id')} has open review; "
                                "the sentence renders it settled")
                for _fam, rec in resolved:
                    if _dead(_fam, rec.get("status") or ""):
                        finding("CONTESTED_AS_SETTLED", sentence,
                                f"{_fam} {rec.get('status')} is not live "
                                "support; the sentence renders it settled")
                # walked / assumption_ids objects, not only direct basis_refs
                # (review R31A C-2)
                store = getattr(projection, "store", None)
                extra_ids = (walked | set(sentence.get("assumption_ids") or ())) \
                    - {r.get("id") for _, r in resolved}
                if store is not None:
                    from curunir_analytic.store import ANALYTIC_ID_FIELDS
                    for wid in extra_ids:
                        rec = None
                        fam = ""
                        if hasattr(store, "current_analytics"):
                            for kind in ANALYTIC_ID_FIELDS:
                                rec = store.current_analytics(kind).get(wid)
                                if rec is not None:
                                    fam = kind
                                    break
                        if rec is None and hasattr(store, "current_hypotheses"):
                            rec = store.current_hypotheses().get(wid)
                            fam = "hypothesis"
                        if rec is not None and _dead(fam, rec.get("status") or ""):
                            finding("CONTESTED_AS_SETTLED", sentence,
                                    f"{fam} {rec.get('status')} is not live "
                                    "support; the sentence renders it settled")
                if sentence.get("temporal_scope") != "HISTORICAL":
                    claims = [r for f, r in resolved if f == "semantic_claim"]
                    store = getattr(projection, "store", None)
                    if store is not None and hasattr(store, "current_claims"):
                        related = _related_claim_ids(store, cite)
                        current = store.current_claims()
                        for cid in related:
                            rec = current.get(cid)
                            if rec is not None \
                                    and projection.get("semantic_claim", cid) is not None \
                                    and rec not in claims:
                                claims.append(rec)
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
            # numeric probability must equal some AUTHORED version of one of
            # the forecasts the sentence references (union across refs, so an
            # honest comparative sentence validates) — word-form
            # probabilities ("three in four") are a stated limitation
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
    """Open dissent on the report OR any of its sentences/sections, across
    EVERY version — dissent anchored below report level must not dodge the
    approval gate, and rewording a sentence must not detach it."""
    part_ids = {report_id}
    for version in projection.versions("workbench_report", report_id):
        for section in version["sections"]:
            part_ids.add(section["section_id"])
            part_ids |= {x["sentence_id"] for x in section["sentences"]}
    return [a for a in projection.family("workbench_annotation")
            if a["kind"] == "DISSENT" and a["status"] == "OPEN"
            and (a["target_id"] in part_ids or a.get("anchor_ref") in part_ids)]


def _claims_from_record(store: WorkbenchStore, record: Mapping[str, Any] | None,
                        related: set, pending: list, seen: set) -> None:
    """Collect claim ids a retained record rests on, and queue analytic
    objects it embeds so the walk covers warning→forecast, indicator→
    forecast, analogue→episode (review R26A-1)."""
    if not record:
        return
    from curunir_analytic.substrate import (_embedded_analytic_ids,
                                            material_claim_ids)
    related.update(material_claim_ids(record))
    for key in ("supporting_claim_ids", "contradicting_claim_ids",
                "unresolved_claim_ids", "outcome_claim_ids", "claim_ids"):
        related.update(x for x in (record.get(key) or ()) if x)
    basis = record.get("basis") if isinstance(record.get("basis"), Mapping) else {}
    related.update(basis.get("supporting_claim_ids") or ())
    related.update(basis.get("contradicting_claim_ids") or ())
    for rid in _embedded_analytic_ids(record):
        if rid and rid not in seen:
            pending.append(rid)
    # workbench author_forecast stores hypothesis/theme links only in
    # proposition_refs (kind, id) — not supporting_claim_ids (R27A-1)
    for key in ("depends_on", "proposition_refs"):
        for reference in record.get(key) or ():
            if isinstance(reference, (list, tuple)) and len(reference) == 2 \
                    and reference[1] and reference[1] not in seen:
                if reference[0] == "claim":
                    related.add(reference[1])
                else:
                    pending.append(reference[1])


def _related_claim_ids(store: WorkbenchStore, cited: set,
                       walked: set | None = None) -> set:
    """Claim ids a cited basis_ref implicates.

    basis_refs are polymorphic — every family `validate_report.resolve`
    accepts (claim / observation / manifestation / forecast / hypothesis /
    theme / …). Walking only observation→claim (R25A-4) left the rest of
    that list fail-open: a PUBLIC forecast whose supporting claim was
    later SPECIAL-RETRACTED shipped (review R26A-1)."""
    related: set = set()
    claims = store.current_claims() if hasattr(store, "current_claims") else {}
    states = store.latest_by_id("semantic_claim_state", "claim_id")
    seen: set = walked if walked is not None else set()
    pending = list(cited)
    current_analytics = getattr(store, "current_analytics", None)
    current_hypotheses = getattr(store, "current_hypotheses", None)
    analytic_kinds: tuple = ()
    if callable(current_analytics):
        from curunir_analytic.store import ANALYTIC_ID_FIELDS
        analytic_kinds = tuple(ANALYTIC_ID_FIELDS)
    while pending:
        ref = pending.pop()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        if ref in claims or ref in states:
            related.add(ref)
        if hasattr(store, "claims_referencing_observation"):
            for claim in store.claims_referencing_observation(ref):
                related.add(claim["claim_id"])
        if hasattr(store, "observations_for_manifestation"):
            for observation in store.observations_for_manifestation(ref):
                oid = observation.get("observation_id")
                if oid and oid not in seen:
                    pending.append(oid)
        if callable(current_analytics):
            for kind in analytic_kinds:
                rec = current_analytics(kind).get(ref)
                if rec is not None:
                    _claims_from_record(store, rec, related, pending, seen)
        if callable(current_hypotheses):
            hyp = current_hypotheses().get(ref)
            if hyp is not None:
                _claims_from_record(store, hyp, related, pending, seen)
        # Reverse edges: an objective is cited but its claims live on the
        # assumptions/paths that name it (create_objective does not copy
        # them onto the objective record) — review R26A-1 leftover.
        if hasattr(store, "current_assumptions"):
            for assumption in store.current_assumptions().values():
                if ref in (assumption.get("objective_ids") or ()):
                    aid = assumption.get("assumption_id")
                    if aid and aid not in seen:
                        pending.append(aid)
        if callable(current_analytics):
            for path in current_analytics("impact_path").values():
                if path.get("objective_id") == ref:
                    pid = path.get("path_id")
                    if pid and pid not in seen:
                        pending.append(pid)
            for warning in current_analytics("strategic_warning").values():
                if ref in (warning.get("objective_id"), warning.get("forecast_id")):
                    wid = warning.get("warning_id")
                    if wid and wid not in seen:
                        pending.append(wid)
    return related


def _raw_hidden_basis_concerns(store: WorkbenchStore, projection: MissionProjection,
                               report: Mapping[str, Any]) -> list[str]:
    """A basis the report cites whose current claim-STATE (a
    retraction/contradiction) or OPEN review the approver is NOT cleared to see
    must BLOCK approval. validate_report reads the approver's FILTERED projection,
    so an invisible retraction/contest is silently counted absent and a published,
    four-eyes-approved report can render retracted evidence as settled fact. This
    is the SAME fail-open-gate class as the dissent gate (a control that BLOCKS
    reads the RAW store, never a filtered view), swept to the basis gate (R23B-3)
    and to every family validate_report accepts as a basis_ref (R25A-4)."""
    cited: set = set()
    for section in report["sections"]:
        cited |= set(section.get("option_ids", ()))
        for sentence in section["sentences"]:
            cited |= set(sentence.get("basis_refs", ()))
            cited |= set(sentence.get("assumption_ids", ()))
    if not cited:
        return []
    from curunir_operational.access import can_view
    approver = projection.context
    hidden: list[str] = []
    walked: set = set()
    related = _related_claim_ids(store, cited, walked)
    # Ask whether the approver can VIEW the CURRENT state, not "has the approver
    # ever seen ANY state record for this claim". semantic_claim_state is an APPEND
    # family, so a benign visible earlier CURRENT state would otherwise MASK a
    # later compartmented RETRACTED and let the approval ship retracted evidence as
    # settled fact. latest_by_id is last-wins = the current state (review B1/R23B-3).
    for claim_id, state in store.latest_by_id("semantic_claim_state", "claim_id").items():
        if claim_id in related and not can_view(state.get("marking"), approver):
            hidden.append(claim_id)
    for item in store.latest_by_id("review_item", "item_id").values():
        if item.get("status") != "OPEN":
            continue
        subject = item.get("subject_id")
        # reviews on intermediate walk nodes (a hypothesis a forecast
        # proposition_refs) must also fail closed (review R28A-3)
        if (subject in cited or subject in related or subject in walked) \
                and not can_view(item.get("marking"), approver):
            hidden.append(subject)
    return hidden


def _raw_open_dissent(store: WorkbenchStore, report_id: str) -> list[dict]:
    """Open dissent on the report or its parts, read from the RAW store (ALL
    markings). An access-filtered VIEW must never silently disarm the control
    that reads it: a dissent the approver is not cleared to SEE must still gate
    the approval, failing CLOSED — exactly as validate_report blocks on basis the
    approver cannot see (UNRESOLVABLE_BASIS), never silently counting it absent
    (review B-3)."""
    part_ids = {report_id}
    for version in store.report_versions(report_id):
        for section in version["sections"]:
            part_ids.add(section["section_id"])
            part_ids |= {x["sentence_id"] for x in section["sentences"]}
    return [a for a in store.current_annotations().values()
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
    content_authors = {v["author"] for v in store.report_versions(report_id)}
    # Robust submitter derivation: the actor who appended the IN_REVIEW VERSION
    # event is the submitter, and that append (`submit_report`'s #1) is durable —
    # whereas the SUBMITTED disposition (#2) can be lost to a crash between the
    # two, which would erase the submitter and permit a self-approval (review
    # C-4). We union the disposition-derived submitters (when present) with the
    # IN_REVIEW-version-event actors. Reviewers who REJECTED
    # (RETURNED_FOR_REVISION) or approved are NOT authors/submitters and stay
    # eligible to approve a later revision — the normal review workflow.
    submitters = {d["actor_id"] for d in store.report_dispositions(report_id)
                  if d["disposition"] == "SUBMITTED"}
    submitters |= {event["actor"] for event in store.events()
                   if event["record"].get("record_type") == "workbench_report"
                   and event["record"].get("report_id") == report_id
                   and event["record"].get("status") == "IN_REVIEW"}
    if actor in content_authors | submitters:
        raise PermissionError(
            "separation of duties: an analyst who authored or submitted this "
            "report cannot also approve it")
    validation = validate_report(projection, current)
    if not validation["ok"]:
        raise ReportValidationError(validation["blocking"])
    # fail CLOSED on a cited basis whose claim-state / open review the approver is
    # not cleared to see: validate_report reads the filtered projection and would
    # silently count it absent, letting an approval assert retracted/contested
    # evidence as settled fact (R23B-3 — the dissent-gate fix, swept to the basis gate)
    hidden_basis = _raw_hidden_basis_concerns(store, projection, current)
    if hidden_basis:
        raise ValueError(
            "this report cites a basis whose claim-state or open review you are "
            "not cleared to view; it must be reviewed and cleared by an actor who "
            "can see it before approval — approval refused")
    dissent = open_dissent(projection, report_id)
    dissent_ids = tuple(a["annotation_id"] for a in dissent)
    # fail CLOSED on dissent the approver is not cleared to see: they cannot
    # acknowledge what they cannot see, and an approval must never assert "no
    # dissent" over a dissent that is merely invisible to the approver (review B-3)
    visible_ids = {a["annotation_id"] for a in dissent}
    hidden = [a for a in _raw_open_dissent(store, report_id)
              if a["annotation_id"] not in visible_ids]
    if hidden:
        raise ValueError(
            "open dissent exists on this report that you are not cleared to view; "
            "it must be resolved by a cleared reviewer, or the report approved by "
            "an actor who can see and acknowledge it — approval refused")
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
                         f"{_html.escape(a['author'])}: {_html.escape(a['text'])} "
                         f"<span class='tag'>[DISSENT/{_html.escape(a['status'])}]</span></p>")
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

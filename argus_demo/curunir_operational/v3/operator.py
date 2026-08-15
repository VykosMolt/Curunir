"""Privacy-bounded operator-study harness and grounded scripted validation."""
from __future__ import annotations

import html
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..canonical import canonical_line, parse_time, sha256
from .models import (GroundedEvaluationResult, OperatorAnswer, OperatorSession,
                     OperatorStudyDefinition, OperatorTask, SubjectiveFeedbackRecord, TaskEvent)


STUDY_MODES = ("TRAINING", "EVALUATION", "BASELINE_COMPARISON", "AFTER_ACTION_REVIEW")
CONDITIONS = ("STATIC_ARTIFACT_BASELINE", "CURUNIR_WORKBENCH")
ALLOWED_EVENT_TYPES = (
    "TASK_STARTED", "TASK_ENDED", "VIEW_CHANGED", "OBJECT_OPENED", "EVIDENCE_TRAVERSED",
    "HYPOTHESIS_VIEWED", "REPORT_GENERATED", "ANSWER_SUBMITTED", "ACCESS_DENIED",
    "FEEDBACK_SUBMITTED",
)
ALLOWED_DETAIL_KEYS = {
    "view_id", "object_id", "evidence_ref", "hypothesis_id", "report_type", "completion_state",
    "access_reason_category", "feedback_category",
}
FORBIDDEN_COLLECTION = (
    "unrelated keystrokes", "private files", "raw desktop activity", "audio", "video",
    "names", "email addresses", "network identifiers", "unnecessary personal data",
)


class StudyError(ValueError):
    pass


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_line(value) + "\n", encoding="utf-8")


def _append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_line(value) + "\n")


class OperatorStudyHarness:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        definition_path = self.root / "study_definition.json"
        if not definition_path.exists():
            raise StudyError(f"study harness is not initialized: {self.root}")
        data = json.loads(definition_path.read_text(encoding="utf-8"))
        data["modes"] = tuple(data["modes"]); data["conditions"] = tuple(data["conditions"])
        data["tasks"] = tuple(data["tasks"]); data["metrics"] = tuple(data["metrics"])
        data["privacy_exclusions"] = tuple(data["privacy_exclusions"])
        self.definition = OperatorStudyDefinition(**data)

    @classmethod
    def initialize(cls, root: str | Path, definition: OperatorStudyDefinition) -> "OperatorStudyHarness":
        root = Path(root)
        if root.exists() and any(root.iterdir()):
            raise StudyError(f"refusing to initialize non-empty study root: {root}")
        if not set(definition.modes) <= set(STUDY_MODES):
            raise StudyError("study definition contains unsupported mode")
        if set(definition.conditions) != set(CONDITIONS):
            raise StudyError("fair comparison requires both baseline and Curunír workbench conditions")
        if not set(FORBIDDEN_COLLECTION) <= set(definition.privacy_exclusions):
            raise StudyError("study privacy exclusions are incomplete")
        root.mkdir(parents=True, exist_ok=True)
        _write(root / "study_definition.json", definition.to_record())
        return cls(root)

    def tasks(self) -> dict[str, OperatorTask]:
        result: dict[str, OperatorTask] = {}
        for record in self.definition.tasks:
            data = dict(record)
            data["evidence_path"] = tuple(data["evidence_path"])
            data["policy_constraints"] = tuple(data["policy_constraints"])
            task = OperatorTask(**data)
            result[task.task_id] = task
        return result

    def start_session(self, session: OperatorSession) -> None:
        if session.mode not in STUDY_MODES or session.condition not in CONDITIONS:
            raise StudyError("invalid study mode or condition")
        if session.study_id != self.definition.study_id:
            raise StudyError("session references another study")
        if not session.anonymized_participant_id.startswith("P-"):
            raise StudyError("participant id must be anonymized")
        path = self.root / "sessions" / session.session_id / "session.json"
        if path.exists():
            raise StudyError("duplicate session id")
        _write(path, session.to_record())

    def record_event(self, event: TaskEvent) -> None:
        if event.event_type not in ALLOWED_EVENT_TYPES:
            raise StudyError(f"study event type is outside privacy boundary: {event.event_type}")
        unknown = set(event.detail) - ALLOWED_DETAIL_KEYS
        if unknown:
            raise StudyError(f"study event detail overcollects fields: {sorted(unknown)}")
        if event.task_id not in self.tasks():
            raise StudyError("study event references unknown task")
        if not (self.root / "sessions" / event.session_id / "session.json").exists():
            raise StudyError("study event references unknown session")
        _append(self.root / "sessions" / event.session_id / "task_events.jsonl", event.to_record())

    def submit_answer(self, session_id: str, answer: OperatorAnswer) -> GroundedEvaluationResult:
        task = self.tasks().get(answer.task_id)
        if not task:
            raise StudyError("answer references unknown task")
        expected = task.expected_answer
        expected_fields = expected.get("fields", expected)
        actual_fields = answer.answer.get("fields", answer.answer)
        correct = all(actual_fields.get(key) == value for key, value in expected_fields.items())
        expected_path = tuple(task.evidence_path)
        actual_path = tuple(answer.answer.get("evidence_path", ()))
        evidence_correct = actual_path == expected_path
        prohibited = set(task.policy_constraints) & set(answer.answer.get("policy_violations", ()))
        policy_compliant = not prohibited
        unresolved = any(value in ("UNRESOLVED", "UNKNOWN", "INSUFFICIENT_EVIDENCE")
                         for value in expected_fields.values())
        certainty = bool(answer.answer.get("claims_resolved", False)) or answer.confidence > .8
        inappropriate = unresolved and certainty
        score = round((.55 if correct else 0) + (.25 if evidence_correct else 0) +
                      (.20 if policy_compliant else 0) - (.20 if inappropriate else 0), 3)
        rationale = (
            f"answer correctness={'pass' if correct else 'fail'}",
            f"evidence trace={'pass' if evidence_correct else 'fail'}",
            f"policy compliance={'pass' if policy_compliant else 'fail'}",
            f"inappropriate certainty={'present' if inappropriate else 'absent'}",
        )
        result = GroundedEvaluationResult(answer.task_id, correct, evidence_correct, policy_compliant,
                                          inappropriate, max(0.0, score), rationale)
        session_root = self.root / "sessions" / session_id
        _append(session_root / "answers.jsonl", answer.to_record())
        _append(session_root / "grounded_results.jsonl", result.to_record())
        return result

    def feedback(self, feedback: SubjectiveFeedbackRecord) -> None:
        for value in (feedback.workload, feedback.trust):
            if value is not None and not 1 <= value <= 7:
                raise StudyError("feedback scales are bounded 1..7")
        _write(self.root / "sessions" / feedback.session_id / "subjective_feedback.json",
               feedback.to_record())

    def end_session(self, session_id: str, ended_at: str) -> OperatorSession:
        path = self.root / "sessions" / session_id / "session.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        session = OperatorSession(**data)
        if parse_time(ended_at) < parse_time(session.started_at):
            raise StudyError("session end precedes start")
        ended = replace(session, ended_at=ended_at)
        _write(path, ended.to_record())
        return ended

    def scoring_export(self) -> dict[str, Any]:
        sessions = []
        for session_path in sorted((self.root / "sessions").glob("*/session.json")):
            session_root = session_path.parent
            results = [json.loads(line) for line in (session_root / "grounded_results.jsonl").read_text(
                encoding="utf-8").splitlines()] if (session_root / "grounded_results.jsonl").exists() else []
            events = [json.loads(line) for line in (session_root / "task_events.jsonl").read_text(
                encoding="utf-8").splitlines()] if (session_root / "task_events.jsonl").exists() else []
            sessions.append({"session": json.loads(session_path.read_text(encoding="utf-8")),
                             "grounded_results": results, "instrumented_event_count": len(events)})
        export = {"study_id": self.definition.study_id, "human_results": "NOT_RUN",
                  "sessions": sessions, "metrics_prepared": list(self.definition.metrics),
                  "privacy_exclusions": list(self.definition.privacy_exclusions)}
        export["integrity_hash"] = sha256(export)
        _write(self.root / "scoring_export.json", export)
        return export


def render_static_baseline(study: OperatorStudyDefinition, situation_report: str,
                           data_table: list[Mapping[str, Any]], exported_map_svg: str,
                           evidence_summary: str) -> str:
    heads = sorted({key for row in data_table for key in row})
    head_html = "".join(f'<th scope="col">{html.escape(key)}</th>' for key in heads)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(key, '')))}</td>" for key in heads) +
                   "</tr>" for row in data_table)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fair static-artifact baseline</title><style>body{{font-family:system-ui;max-width:1050px;margin:1rem auto;padding:.8rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #888;padding:.3rem;text-align:left}}section{{margin:1.2rem 0}}svg{{max-width:100%;height:auto;border:1px solid #888}}</style></head>
<body><main><h1>Static-artifact baseline</h1><p>Study {html.escape(study.study_id)}. This condition contains the same synthetic evidence and operational content without interactive traversal.</p>
<section><h2>Situation report</h2><pre>{html.escape(situation_report)}</pre></section>
<section><h2>Data table</h2><table><caption>Operational data available to baseline participants</caption><thead><tr>{head_html}</tr></thead><tbody>{body}</tbody></table></section>
<section><h2>Exported map</h2>{exported_map_svg}</section><section><h2>Evidence summary</h2><p>{html.escape(evidence_summary)}</p></section>
</main></body></html>"""


def render_harness(study: OperatorStudyDefinition, workbench_links: Mapping[str, str]) -> str:
    tasks = "".join(
        f'<article class="task" tabindex="0" data-task="{html.escape(task["task_id"])}"><h2>{html.escape(task["task_id"])}</h2>'
        f'<p>{html.escape(task["prompt"])}</p><button type="button" data-event="TASK_STARTED">Start task</button> '
        f'<button type="button" data-event="ANSWER_SUBMITTED">Record submission</button></article>'
        for task in study.tasks)
    links = "".join(f'<li><a href="{html.escape(path)}">{html.escape(label)}</a></li>'
                    for label, path in workbench_links.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curunír operator evaluation harness</title><style>body{{font-family:system-ui;max-width:1000px;margin:1rem auto;padding:.8rem}}.notice{{border:2px solid #754;padding:.6rem}}.task{{border:1px solid #777;padding:.7rem;margin:.7rem 0}}button,a{{font:inherit;padding:.35rem}}:focus{{outline:3px solid #c65;outline-offset:2px}}</style></head>
<body><main><div class="notice">SCRIPTED/HUMAN-STUDY HARNESS · NO HUMAN RESULTS RECORDED · RESEARCH SHADOW</div>
<h1>Operator tasks</h1><p>The harness records task/view/evidence events only. It does not collect keystrokes, desktop activity, private files, audio or video.</p>
<nav aria-label="Study conditions"><ul><li><a href="baseline.html">Fair static-artifact baseline</a></li>{links}</ul></nav>{tasks}
<button id="export" type="button">Export this browser's bounded event log</button><pre id="status" aria-live="polite"></pre>
</main><script>
const allowed=new Set({json.dumps(list(ALLOWED_EVENT_TYPES))}); const events=[];
document.querySelectorAll('[data-event]').forEach(button=>button.addEventListener('click',()=>{{
 const kind=button.dataset.event;if(!allowed.has(kind))return;events.push({{event_type:kind,task_id:button.closest('[data-task]').dataset.task,time:new Date().toISOString()}});
 document.getElementById('status').textContent=`${{events.length}} bounded study event(s) recorded in browser memory.`;
}}));
document.getElementById('export').addEventListener('click',()=>{{const blob=new Blob([JSON.stringify(events,null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='operator-study-events.json';a.click();URL.revokeObjectURL(a.href);}});
</script></body></html>"""


def scripted_validation(harness: OperatorStudyHarness, session: OperatorSession,
                        *, start_time: str, end_time: str) -> dict[str, Any]:
    harness.start_session(session)
    results = []
    for index, task in enumerate(harness.tasks().values(), start=1):
        base = f"{start_time[:-6]}" if start_time.endswith("+00:00") else start_time
        event_time = start_time
        harness.record_event(TaskEvent(f"te-{index}-start", session.session_id, task.task_id,
                                       "TASK_STARTED", event_time, {"view_id": session.condition}))
        if task.evidence_path:
            harness.record_event(TaskEvent(f"te-{index}-evidence", session.session_id, task.task_id,
                                           "EVIDENCE_TRAVERSED", event_time,
                                           {"evidence_ref": task.evidence_path[0]}))
        answer = OperatorAnswer(task.task_id,
                                {"fields": dict(task.expected_answer.get("fields", task.expected_answer)),
                                 "evidence_path": list(task.evidence_path), "policy_violations": []},
                                confidence=.75, completion_state="COMPLETED")
        results.append(harness.submit_answer(session.session_id, answer).to_record())
        harness.record_event(TaskEvent(f"te-{index}-end", session.session_id, task.task_id,
                                       "TASK_ENDED", event_time, {"completion_state": "COMPLETED"}))
    harness.end_session(session.session_id, end_time)
    export = harness.scoring_export()
    result = {
        "validation": "SCRIPTED_OPERATOR_HARNESS_VALIDATION",
        "status": "SCRIPTED_HARNESS_VALID" if all(item["correct"] and item["evidence_trace_correct"]
                                                     and item["policy_compliant"] for item in results) else "INVALID",
        "task_count": len(results), "results": results,
        "instrumentation_works": all(item["instrumented_event_count"] >= 2 for item in export["sessions"]),
        "human_study_status": "HUMAN_STUDY_PENDING",
        "claim_boundary": "scripted validation is not evidence of human performance improvement",
    }
    _write(harness.root / "scripted_validation_result.json", result)
    return result

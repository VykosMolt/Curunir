"""Workbench store: the analytic store plus collaboration/report event types.

Same single hash chain, same export/import/replay and tamper-detection
guarantees. Annotations and reports are versioned families with strict
next-version enforcement, so a concurrent editor's stale write raises a
conflict instead of silently shadowing another analyst's work. Dispositions
are append-only history: an approved report version is never rewritten.
"""
from __future__ import annotations

from curunir_analytic.store import AnalyticStore
from curunir_identity.contracts import IDENTITY_EVENT_TYPES

WORKBENCH_EVENT_TYPES = {
    "WORKBENCH_ANNOTATION_RECORDED": "workbench_annotation",
    "WORKBENCH_REPORT_RECORDED": "workbench_report",
    "WORKBENCH_REPORT_DISPOSITION_RECORDED": "workbench_report_disposition",
    "WORKBENCH_SAVED_VIEW_RECORDED": "workbench_saved_view",
}


class WorkbenchStore(AnalyticStore):
    # the identity plane's key registry and signed actions live in the same
    # hash-chained log, so a mission's signed acts stay verifiable on replay
    EVENT_TYPES = {**AnalyticStore.EVENT_TYPES, **WORKBENCH_EVENT_TYPES,
                   **IDENTITY_EVENT_TYPES}
    # EXTENDS the analytic plane's map — replacing it would silently strip
    # stale-writer protection from the families the shipped stack runs on.
    # actor_key is versioned by key_id (enroll → revoke/retire append versions);
    # signed_action is append-only immutable attribution.
    VERSIONED_RECORD_TYPES = {
        **AnalyticStore.VERSIONED_RECORD_TYPES,
        "workbench_annotation": "annotation_id",
        "workbench_report": "report_id",
        "workbench_saved_view": "view_id",
        "actor_key": "key_id",
    }

    # ---- replayed views over workbench records ---------------------------

    def current_annotations(self) -> dict[str, dict]:
        """Latest version per annotation_id; every prior version stays in the log."""
        return self.latest_by_id("workbench_annotation", "annotation_id")

    def current_reports(self) -> dict[str, dict]:
        """Latest version per report_id; every prior version stays in the log."""
        return self.latest_by_id("workbench_report", "report_id")

    def report_versions(self, report_id: str) -> list[dict]:
        """Full version history of one report, oldest first."""
        return [r for r in self.records_of("workbench_report")
                if r["report_id"] == report_id]

    def report_dispositions(self, report_id: str) -> list[dict]:
        return [r for r in self.records_of("workbench_report_disposition")
                if r["report_id"] == report_id]

"""The analytic store plus the annotation, report and saved-view record types.

Annotations and reports are versioned, so a second editor working from a stale
copy gets a conflict instead of quietly overwriting the first. Dispositions are
append-only: an approved report version is never rewritten.
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
    EVENT_TYPES = {
        **AnalyticStore.EVENT_TYPES,
        **WORKBENCH_EVENT_TYPES,
        **IDENTITY_EVENT_TYPES,
    }
    # Extend the inherited map; replacing it would drop stale-write
    # protection from every family below.
    VERSIONED_RECORD_TYPES = {
        **AnalyticStore.VERSIONED_RECORD_TYPES,
        "workbench_annotation": "annotation_id",
        "workbench_report": "report_id",
        "workbench_saved_view": "view_id",
        "actor_key": "key_id",
    }

    # ---- views over workbench records ----

    def current_annotations(self) -> dict[str, dict]:
        """Latest version of each annotation; earlier versions stay in the log."""
        return self.latest_by_id("workbench_annotation", "annotation_id")

    def current_reports(self) -> dict[str, dict]:
        """Latest version of each report; earlier versions stay in the log."""
        return self.latest_by_id("workbench_report", "report_id")

    def report_versions(self, report_id: str) -> list[dict]:
        """Full version history of one report, oldest first."""
        return [r for r in self.records_of("workbench_report")
                if r["report_id"] == report_id]

    def report_dispositions(self, report_id: str) -> list[dict]:
        return [r for r in self.records_of("workbench_report_disposition")
                if r["report_id"] == report_id]

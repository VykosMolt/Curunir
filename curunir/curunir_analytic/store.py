"""Analytic store: the semantic store plus the analytical event types.

One store root carries the whole stack under a single hash chain, with the
inherited export, import, replay and tamper-detection guarantees. Every
"current state" view here is a pure function over the replayed log.
"""
from __future__ import annotations

from curunir_semantic.store import SemanticStore

from . import contracts  # noqa: F401  registers this package's record types

ANALYTIC_EVENT_TYPES = {
    "ANALYTIC_THEME_RECORDED": "analytic_theme",
    "ANALYTIC_NARRATIVE_RECORDED": "analytic_narrative",
    "NARRATIVE_VARIANT_RECORDED": "narrative_variant",
    "PROPAGATION_EDGE_RECORDED": "propagation_edge",
    "STAKEHOLDER_ASSESSMENT_RECORDED": "stakeholder_assessment",
    "INFLUENCE_ASSERTION_RECORDED": "influence_assertion",
    "MISSION_OBJECTIVE_RECORDED": "mission_objective",
    "ANALYTIC_ASSUMPTION_RECORDED": "analytic_assumption",
    "IMPACT_PATH_RECORDED": "impact_path",
    "RESPONSE_OPTION_RECORDED": "response_option",
    "ANALYTIC_TRANSITION_RECORDED": "analytic_transition",
    "HISTORICAL_EPISODE_RECORDED": "historical_episode",
    "HISTORICAL_ANALOGUE_RECORDED": "historical_analogue",
    "ANALYTIC_FORECAST_RECORDED": "analytic_forecast",
    "FORECAST_INDICATOR_RECORDED": "forecast_indicator",
    "STRATEGIC_WARNING_RECORDED": "strategic_warning",
}

# record_type → (event type, id field) for the generic version helpers
ANALYTIC_ID_FIELDS = {
    "analytic_theme": ("ANALYTIC_THEME_RECORDED", "theme_id"),
    "analytic_narrative": ("ANALYTIC_NARRATIVE_RECORDED", "narrative_id"),
    "narrative_variant": ("NARRATIVE_VARIANT_RECORDED", "variant_id"),
    "propagation_edge": ("PROPAGATION_EDGE_RECORDED", "edge_id"),
    "stakeholder_assessment": ("STAKEHOLDER_ASSESSMENT_RECORDED", "assessment_id"),
    "influence_assertion": ("INFLUENCE_ASSERTION_RECORDED", "influence_id"),
    "mission_objective": ("MISSION_OBJECTIVE_RECORDED", "objective_id"),
    "analytic_assumption": ("ANALYTIC_ASSUMPTION_RECORDED", "assumption_id"),
    "impact_path": ("IMPACT_PATH_RECORDED", "path_id"),
    "response_option": ("RESPONSE_OPTION_RECORDED", "option_id"),
    "historical_episode": ("HISTORICAL_EPISODE_RECORDED", "episode_id"),
    "historical_analogue": ("HISTORICAL_ANALOGUE_RECORDED", "analogue_id"),
    "analytic_forecast": ("ANALYTIC_FORECAST_RECORDED", "forecast_id"),
    "forecast_indicator": ("FORECAST_INDICATOR_RECORDED", "indicator_id"),
    "strategic_warning": ("STRATEGIC_WARNING_RECORDED", "warning_id"),
}


class AnalyticStore(SemanticStore):
    EVENT_TYPES = {**SemanticStore.EVENT_TYPES, **ANALYTIC_EVENT_TYPES}
    # Extends the semantic map rather than replacing it, so a concurrent
    # writer's stale update raises on every family, analytical and semantic.
    VERSIONED_RECORD_TYPES = {
        **SemanticStore.VERSIONED_RECORD_TYPES,
        **{record_type: id_field
           for record_type, (_, id_field) in ANALYTIC_ID_FIELDS.items()},
    }

    # ---- log-derived indexes ---------------------------------------------

    def _reset_indexes(self) -> None:
        super()._reset_indexes()
        # A candidate is spent by the first record that carried it. Keyed over
        # the whole log, so a later version dropping the id cannot free it.
        self._proposal_materializations: dict[str, tuple[str, str]] = {}
        self._transition_ids: set[str] = set()

    def _index_record(self, event: dict) -> None:
        super()._index_record(event)
        record = event["record"]
        record_type = record["record_type"]
        if record_type == "analytic_transition":
            self._transition_ids.add(record["transition_id"])
        elif record_type in ANALYTIC_ID_FIELDS:
            proposal_id = record.get("proposal_id")
            if proposal_id:
                _, id_field = ANALYTIC_ID_FIELDS[record_type]
                self._proposal_materializations.setdefault(
                    proposal_id, (record_type, record[id_field]))

    def materialization_of(self, proposal_id: str) -> tuple[str, str] | None:
        """(record type, id) of what already spent this candidate proposal."""
        return self._proposal_materializations.get(proposal_id)

    def has_transition(self, transition_id: str) -> bool:
        return transition_id in self._transition_ids

    # ---- generic versioned views ----------------------------------------

    def current_analytics(self, record_type: str) -> dict[str, dict]:
        """Latest version per object id; every prior version stays in the log."""
        _, id_field = ANALYTIC_ID_FIELDS[record_type]
        current: dict[str, dict] = {}
        for record in self.records_of(record_type):
            known = current.get(record[id_field])
            if known is None or record.get("version", 1) >= known.get("version", 1):
                current[record[id_field]] = record
        return current

    def analytic_versions(self, record_type: str, object_id: str) -> list[dict]:
        """Full version history of one analytical object, oldest first."""
        _, id_field = ANALYTIC_ID_FIELDS[record_type]
        return [r for r in self.records_of(record_type) if r[id_field] == object_id]

    def next_analytic_version(self, record_type: str, object_id: str) -> int:
        versions = self.analytic_versions(record_type, object_id)
        return (max(r.get("version", 1) for r in versions) + 1) if versions else 1

    def transitions_for(self, subject_id: str) -> list[dict]:
        return [r for r in self.records_of("analytic_transition")
                if r["subject_id"] == subject_id]

    # ---- typed convenience views ----------------------------------------

    def current_themes(self) -> dict[str, dict]:
        return self.current_analytics("analytic_theme")

    def current_narratives(self) -> dict[str, dict]:
        return self.current_analytics("analytic_narrative")

    def current_stakeholder_assessments(self) -> dict[str, dict]:
        return self.current_analytics("stakeholder_assessment")

    def current_influence_assertions(self) -> dict[str, dict]:
        return self.current_analytics("influence_assertion")

    def current_objectives(self) -> dict[str, dict]:
        return self.current_analytics("mission_objective")

    def current_assumptions(self) -> dict[str, dict]:
        return self.current_analytics("analytic_assumption")

    def current_impact_paths(self) -> dict[str, dict]:
        return self.current_analytics("impact_path")

    def variants_for_narrative(self, narrative_id: str) -> list[dict]:
        return [r for r in self.current_analytics("narrative_variant").values()
                if r["narrative_id"] == narrative_id]

    def propagation_for_narrative(self, narrative_id: str) -> list[dict]:
        return [r for r in self.current_analytics("propagation_edge").values()
                if r["narrative_id"] == narrative_id]

    def paths_for_objective(self, objective_id: str) -> list[dict]:
        return [r for r in self.current_impact_paths().values()
                if r["objective_id"] == objective_id]

    def current_forecasts(self) -> dict[str, dict]:
        return self.current_analytics("analytic_forecast")

    def current_indicators(self) -> dict[str, dict]:
        return self.current_analytics("forecast_indicator")

    def current_warnings(self) -> dict[str, dict]:
        return self.current_analytics("strategic_warning")

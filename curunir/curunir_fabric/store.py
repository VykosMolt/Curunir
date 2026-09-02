"""The mission store plus the fabric event types."""
from __future__ import annotations

from curunir_operational.store import EVENT_TYPES as MISSION_EVENT_TYPES
from curunir_operational.store import MissionDataStore

from . import contracts  # noqa: F401  registers this package's record types

FABRIC_EVENT_TYPES = {
    "FABRIC_SOURCE_DESCRIBED": "fabric_source_descriptor",
    "FABRIC_SOURCE_PROFILE_RECORDED": "fabric_source_profile",
    "FABRIC_SOURCE_STATUS_RECORDED": "fabric_source_status",
    "FABRIC_NEED_RECORDED": "fabric_information_need",
    "FABRIC_PLAN_RECORDED": "fabric_discovery_plan",
    "FABRIC_EXECUTION_RECORDED": "fabric_execution",
    "FABRIC_MANIFESTATION_RECORDED": "fabric_manifestation",
    "FABRIC_PIVOT_RECORDED": "fabric_pivot",
    "FABRIC_COVERAGE_ASSESSED": "fabric_coverage",
    "FABRIC_WATCH_RECORDED": "fabric_watch",
    "FABRIC_WATCH_RUN_RECORDED": "fabric_watch_run",
    "FABRIC_CHANGE_OBSERVED": "fabric_change",
}


class FabricStore(MissionDataStore):
    EVENT_TYPES = {**MISSION_EVENT_TYPES, **FABRIC_EVENT_TYPES}

    def latest_by_id(self, record_type: str, id_field: str) -> dict[str, dict]:
        """The last record appended for each id."""
        current: dict[str, dict] = {}
        for record in self.records_of(record_type):
            current[record[id_field]] = record
        return current

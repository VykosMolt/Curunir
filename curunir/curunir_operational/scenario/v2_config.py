"""V2 configuration: hazard ingestion (GDACS-shaped), the second workbench,
schema-evolution fixtures, and the shared object fabric setup. Data only."""
from __future__ import annotations

from curunir_operational.access import AccessContext, Marking

# ---- markings and access contexts ------------------------------------------

CIVDEF = "CIVDEF-AUTH"
BASE_MARKING = Marking(owning_authority=CIVDEF, releasability=("CORRIDOR-OPS",))
RESTRICTED_INFRA = Marking(owning_authority=CIVDEF, compartments=("SENSITIVE-INFRA",),
                           releasability=("CORRIDOR-OPS",), min_role="ANALYST")
ENGINEERING = Marking(owning_authority=CIVDEF, compartments=("ENGINEERING-ASSESSMENT",),
                      releasability=("CORRIDOR-OPS",), min_role="ANALYST")
PUBLIC_EVIDENCE = Marking(owning_authority=CIVDEF, releasability=("PUBLIC-EVIDENCE", "CORRIDOR-OPS"))

# six V2 access contexts
CONTEXTS = {
    "logistics": AccessContext("ctx-log", "log-analyst", "HUMAN", ("ANALYST",), (),
                               ("CORRIDOR-OPS",), CIVDEF),
    "civil_protection": AccessContext("ctx-cp", "cp-analyst", "HUMAN", ("ANALYST",), ("SENSITIVE-INFRA",),
                                      ("CORRIDOR-OPS",), CIVDEF),
    "joint": AccessContext("ctx-joint", "joint-coordinator", "HUMAN", ("SUPERVISOR",),
                           ("SENSITIVE-INFRA", "ENGINEERING-ASSESSMENT"), ("CORRIDOR-OPS",), CIVDEF),
    "reviewer": AccessContext("ctx-rev", "privileged-reviewer", "HUMAN", ("SUPERVISOR",),
                              ("SENSITIVE-INFRA", "ENGINEERING-ASSESSMENT"), ("CORRIDOR-OPS", "PUBLIC-EVIDENCE"), CIVDEF),
    "public_evidence": AccessContext("ctx-pub", "public-evidence-viewer", "HUMAN", ("OBSERVER",), (),
                                     ("PUBLIC-EVIDENCE",), CIVDEF),
    "rules": AccessContext("ctx-rules", "rule-engine", "SERVICE", ("ANALYST",),
                           ("SENSITIVE-INFRA", "ENGINEERING-ASSESSMENT"), ("CORRIDOR-OPS",), CIVDEF),
}

STALENESS_HOURS = {"RESOURCE_STOCK": 36.0, "OBSERVATION": 48.0, "INFRASTRUCTURE": 96.0,
                   "ROUTE": 96.0, "MOVEMENT": 48.0, "OPERATIONAL_CONCERN": 72.0}

# ---- hazard schema (GDACS FeatureCollection shape) -------------------------

HAZARD_SCHEMA = {
    "schema_id": "gdacs-hazard", "version": "1.0", "media_type": "application/geo+json",
    "payload_kind": "geojson", "description": "GDACS-shaped hazard event feature",
    "fields": {"eventid": {"type": "integer", "required": True},
               "eventtype": {"type": "string", "required": True, "enum": ["EQ", "FL", "TC", "DR", "WF", "VO"]},
               "name": {"type": "string", "required": True},
               "alertlevel": {"type": "string", "required": True},
               "fromdate": {"type": "string", "required": True},
               "country": {"type": "string", "required": False}},
}

HAZARD_MAPPING = {
    "mapping_id": "hazard-map", "version": "1.0", "input_schema_id": "gdacs-hazard",
    "input_schema_version": "1.0", "output_object_type": "OPERATIONAL_CONCERN",
    "entries": [
        {"source_field": "eventid", "target": "external_id", "transform": "to_string"},
        {"source_field": "name", "target": "label"},
        {"source_field": "eventtype", "target": "attributes.hazard_kind"},
        {"source_field": "alertlevel", "target": "attributes.alert_level"},
        {"source_field": "country", "target": "attributes.country", "on_missing": "SKIP"},
        {"source_field": "fromdate", "target": "valid_from", "transform": "naive_utc"},
        {"source_field": "fromdate", "target": "source_time", "transform": "naive_utc"}],
    "lossy_operations": ["GDACS severitydata and episode detail not carried into the operational object"],
    "quality_effects": {"mapping_confidence": 0.7, "temporal_precision": "DAY",
                        "geospatial_precision": "EVENT_CENTROID_ONLY"},
}

HAZARD_PIPELINE = {
    "pipeline_id": "hazard-ingest", "version": "1.0", "connector_id": "conn-gdacs",
    "connector_kind": "geojson", "schema_id": "gdacs-hazard", "schema_version": "1.0",
    "mode": "direct_state", "unit": "features",
    "object": {"object_type": "OPERATIONAL_CONCERN", "id_prefix": "hazard-", "external_system": "GDACS",
               "external_id_field": "eventid", "epistemic_default": "REPORTED",
               "external_refs_identity_bearing": True},
    "mappings": [{"mapping_id": "hazard-map", "version": "1.0"}],
    "marking": BASE_MARKING.to_record(),
}

# ---- schema evolution: engineering assessment feed v1 → v1.1 → v2 ----------

ENGINEERING_SCHEMA_V1 = {
    "schema_id": "eng-assessment", "version": "1.0", "media_type": "application/json",
    "payload_kind": "document", "description": "engineering assessment of an asset",
    "fields": {"assessment_id": {"type": "string", "required": True},
               "asset": {"type": "string", "required": True},
               "condition": {"type": "string", "required": True, "enum": ["SOUND", "DEGRADED", "FAILED"]},
               "assessed_time": {"type": "string", "required": True, "format": "iso-datetime"}},
}
# v1.1: adds an optional field, backward compatible
ENGINEERING_SCHEMA_V1_1 = {**ENGINEERING_SCHEMA_V1, "version": "1.1", "compatible_with": ["1.0"],
                           "fields": {**ENGINEERING_SCHEMA_V1["fields"],
                                      "inspector": {"type": "string", "required": False}}}
# v2: renames asset→asset_ref, changes the condition enum, incompatible
ENGINEERING_SCHEMA_V2 = {
    "schema_id": "eng-assessment", "version": "2.0", "media_type": "application/json",
    "payload_kind": "document", "description": "engineering assessment v2 (renamed + new enum)",
    "compatible_with": [],
    "fields": {"assessment_id": {"type": "string", "required": True},
               "asset_ref": {"type": "string", "required": True},
               "condition": {"type": "string", "required": True,
                             "enum": ["SOUND", "MONITOR", "RESTRICTED", "CLOSED"]},
               "inspector": {"type": "string", "required": False},
               "assessed_time": {"type": "string", "required": True, "format": "iso-datetime"}},
}

ENGINEERING_MAPPING_V1 = {
    "mapping_id": "eng-map", "version": "1.0", "input_schema_id": "eng-assessment",
    "input_schema_version": "1.0", "output_object_type": "OBSERVATION",
    "entries": [{"source_field": "assessment_id", "target": "external_id"},
                {"source_field": "asset", "target": "attributes.subject_ref"},
                {"source_field": "condition", "target": "attributes.reported_status"},
                {"source_field": "inspector", "target": "attributes.inspector", "on_missing": "SKIP"},
                {"source_field": "assessed_time", "target": "valid_from", "transform": "iso_time"},
                {"source_field": "assessed_time", "target": "source_time", "transform": "iso_time"}],
    "quality_effects": {"mapping_confidence": 0.9},
}
# v2 mapping handles the renamed field explicitly
ENGINEERING_MAPPING_V2 = {
    "mapping_id": "eng-map", "version": "2.0", "input_schema_id": "eng-assessment",
    "input_schema_version": "2.0", "output_object_type": "OBSERVATION",
    "entries": [{"source_field": "assessment_id", "target": "external_id"},
                {"source_field": "asset_ref", "target": "attributes.subject_ref"},
                {"source_field": "condition", "target": "attributes.reported_status"},
                {"source_field": "inspector", "target": "attributes.inspector", "on_missing": "SKIP"},
                {"source_field": "assessed_time", "target": "valid_from", "transform": "iso_time"},
                {"source_field": "assessed_time", "target": "source_time", "transform": "iso_time"}],
    "quality_effects": {"mapping_confidence": 0.9},
}

ENGINEERING_PIPELINE_V1 = {
    "pipeline_id": "engineering-assessments", "version": "1.0", "connector_id": "conn-eng",
    "connector_kind": "json", "event_id_field": "assessment_id", "schema_id": "eng-assessment",
    "schema_version": "1.0", "mode": "observation", "unit": "document",
    "object": {"object_type": "OBSERVATION", "id_prefix": "eng-", "external_system": "ENGINEERING",
               "external_id_field": "assessment_id", "epistemic_default": "REPORTED"},
    "mappings": [{"mapping_id": "eng-map", "version": "1.0"}],
    "observation": {"target_field": "asset", "target_prefix": "infra-"},
    "marking": ENGINEERING.to_record(),
}
ENGINEERING_PIPELINE_V2 = {**ENGINEERING_PIPELINE_V1, "version": "2.0", "schema_version": "2.0",
                           "mappings": [{"mapping_id": "eng-map", "version": "2.0"}],
                           "observation": {"target_field": "asset_ref", "target_prefix": "infra-"}}

# ---- second workbench ------------------------------------------------------

INFRASTRUCTURE_WORKBENCH = {
    "workshop_id": "INFRASTRUCTURE_RESILIENCE_AND_CIVIL_PROTECTION_WORKBENCH_V2", "version": "1.0",
    "purpose": "Infrastructure resilience and civil protection over the shared corridor fabric",
    "object_types": ["INFRASTRUCTURE", "ROUTE", "OPERATIONAL_CONCERN", "OBSERVATION", "RESOURCE_STOCK"],
    "relationship_types": ["AFFECTS", "DEPENDS_ON", "REPORTS_ON", "CONFLICTS_WITH", "ALTERNATE_OF",
                           "LOCATED_AT", "SUPPLIES"],
    "tables": [
        {"table_id": "infrastructure", "title": "Infrastructure status", "object_type": "INFRASTRUCTURE",
         "columns": [{"header": "id", "path": "object_id"}, {"header": "kind", "path": "attributes.kind"},
                     {"header": "status", "path": "attributes.status"},
                     {"header": "state", "path": "epistemic_state"},
                     {"header": "quality", "path": "quality_summary.state"},
                     {"header": "fresh", "path": "freshness.state"}], "sort_by": "object_id"},
        {"table_id": "hazards", "title": "Active hazards", "object_type": "OPERATIONAL_CONCERN",
         "columns": [{"header": "id", "path": "object_id"}, {"header": "kind", "path": "attributes.hazard_kind"},
                     {"header": "alert", "path": "attributes.alert_level"},
                     {"header": "state", "path": "epistemic_state"}], "sort_by": "object_id"},
        {"table_id": "resources", "title": "Relevant resource stock", "object_type": "RESOURCE_STOCK",
         "columns": [{"header": "id", "path": "object_id"}, {"header": "commodity", "path": "attributes.commodity"},
                     {"header": "quantity", "path": "attributes.quantity"},
                     {"header": "fresh", "path": "freshness.state"}], "sort_by": "object_id"}],
    "relationship_tables": [
        {"table_id": "dependencies", "title": "Dependencies and impacts",
         "relation_types": ["AFFECTS", "DEPENDS_ON", "CONFLICTS_WITH"]}],
    "map_layers": [
        {"layer_id": "infrastructure", "title": "Facilities", "object_types": ["INFRASTRUCTURE"],
         "geometry_kinds": ["POINT"]},
        {"layer_id": "hazards", "title": "Hazards", "object_types": ["OPERATIONAL_CONCERN"],
         "geometry_kinds": ["POINT"]},
        {"layer_id": "routes", "title": "Routes", "object_types": ["ROUTE"], "geometry_kinds": ["LINESTRING"]}],
    "timeline": {"include": ["object_version", "alert", "recommendation", "decision", "analyst_action"]},
    "workflow": {"include_requirements": True, "include_tasks": True, "include_evidence_requests": True},
    "alert_rules": ["rule-hazard-impact", "rule-infrastructure-conflict", "rule-reported-disruption",
                    "rule-source-dependence"],
    "actions": ["ACKNOWLEDGE", "ANNOTATE", "ACCEPT", "REJECT", "DEFER", "ESCALATE"],
    "access": {"min_role": "OBSERVER"},
    "reports": ["json", "markdown", "text"],
}

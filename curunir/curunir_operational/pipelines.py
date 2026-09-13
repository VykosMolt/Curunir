"""Mission pipelines: connector, validation, mapping, then objects, relationships
and transformation lineage.

A pipeline definition is strict data — unknown keys are rejected and no code comes
from configuration. The executor is generic, so a new feed means a new schema,
mapping and pipeline rather than an edit here.
"""
from __future__ import annotations

from typing import Any, Mapping

from .access import Marking, marking_from_record
from .argus_adapter import ArgusEvidenceConnector, evidence_ref_from_bundle
from .canonical import digest_id, sha256
from .connectors import (CsvFeedConnector, GeoJsonConnector, JsonFeedConnector,
                         MissionDataConnector, source_watermark)
from .contracts import (EPISTEMIC_STATES, LIFECYCLES, OBJECT_TYPES, RELATION_TYPES, ExternalRef, ObjectVersion,
                        ProvenanceSummary, RelationshipVersion, TransformationRecord)
from .geometry import geometry_from_geojson
from .schema_registry import SchemaError, SchemaRegistry, apply_mapping
from .store import MissionDataStore

PIPELINE_MODES = ("direct_state", "observation", "evidence_observation")
PIPELINE_UNITS = ("document", "rows", "features")
CONNECTOR_KINDS = ("json", "csv", "geojson", "argus_evidence")
IMPLEMENTATION_ID = "curunir_operational.pipelines.PipelineExecutor"
IMPLEMENTATION_VERSION = "1.0"


def build_connector(kind: str, connector_id: str, schema_id: str, event_id_field: str | None = None):
    classes = {"json": JsonFeedConnector, "csv": CsvFeedConnector, "geojson": GeoJsonConnector,
               "argus_evidence": ArgusEvidenceConnector}
    if kind not in classes:
        raise SchemaError(f"unknown connector kind: {kind}")
    return classes[kind](connector_id, schema_id, event_id_field)


def executor_with_registered_connectors(store: MissionDataStore, registry: SchemaRegistry) -> "PipelineExecutor":
    """Rebuild an executor from the registered pipeline definitions alone."""
    connectors = {}
    for definition in store.records_of("pipeline_definition"):
        connectors[definition["connector_id"]] = build_connector(
            definition.get("connector_kind", "json"), definition["connector_id"],
            definition["schema_id"], definition.get("event_id_field"))
    return PipelineExecutor(store, registry, connectors)


def validate_pipeline_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"record_type", "pipeline_id", "version", "connector_id", "connector_kind", "event_id_field",
               "schema_id", "schema_version", "mode", "unit", "object", "mappings", "relationships",
               "observation", "marking", "quality_defaults", "definition_sha256"}
    unknown = set(definition) - allowed
    if unknown:
        raise SchemaError(f"unknown pipeline keys: {sorted(unknown)}")
    for key in ("pipeline_id", "version", "connector_id", "schema_id", "schema_version", "mode", "unit", "object", "mappings", "marking"):
        if key not in definition:
            raise SchemaError(f"pipeline missing {key}")
    if definition["mode"] not in PIPELINE_MODES:
        raise SchemaError(f"unknown pipeline mode: {definition['mode']}")
    if definition.get("connector_kind", "json") not in CONNECTOR_KINDS:
        raise SchemaError(f"unknown connector kind: {definition.get('connector_kind')}")
    if definition["unit"] not in PIPELINE_UNITS:
        raise SchemaError(f"unknown pipeline unit: {definition['unit']}")
    obj = definition["object"]
    extra = set(obj) - {"object_type", "id_prefix", "external_system", "external_id_field",
                        "epistemic_default", "lifecycle", "point_fields", "external_refs_identity_bearing"}
    if extra:
        raise SchemaError(f"unknown object config keys: {sorted(extra)}")
    if "point_fields" in obj and set(obj["point_fields"]) != {"lon", "lat"}:
        raise SchemaError("point_fields needs exactly lon and lat")
    if obj.get("object_type") not in OBJECT_TYPES:
        raise SchemaError(f"unknown object type: {obj.get('object_type')}")
    if obj.get("epistemic_default", "REPORTED") not in EPISTEMIC_STATES:
        raise SchemaError(f"unknown epistemic default: {obj.get('epistemic_default')}")
    if obj.get("lifecycle", "ACTIVE") not in LIFECYCLES:
        raise SchemaError(f"unknown lifecycle: {obj.get('lifecycle')}")
    for rule in definition.get("relationships", []):
        extra = set(rule) - {"relation_type", "target_field", "target_fixed", "target_prefix", "direction"}
        if extra:
            raise SchemaError(f"unknown relationship rule keys: {sorted(extra)}")
        if rule.get("relation_type") not in RELATION_TYPES:
            raise SchemaError(f"unknown relation type: {rule.get('relation_type')}")
        if not rule.get("target_field") and not rule.get("target_fixed"):
            raise SchemaError("relationship rule needs target_field or target_fixed")
        if rule.get("direction", "OUT") not in ("OUT", "IN"):
            raise SchemaError(f"unknown direction: {rule.get('direction')}")
    if definition["mode"] in ("observation", "evidence_observation"):
        observation = definition.get("observation") or {}
        extra = set(observation) - {"target_field", "target_prefix", "prefix_map_field", "prefix_map", "reported_field"}
        if extra:
            raise SchemaError(f"unknown observation config keys: {sorted(extra)}")
        if not observation.get("target_field"):
            raise SchemaError("observation pipelines need observation.target_field")
        if ("prefix_map" in observation) != ("prefix_map_field" in observation):
            raise SchemaError("prefix_map and prefix_map_field go together")
    marking_from_record(definition["marking"])  # refuse an invalid marking
    result = dict(definition)
    result["record_type"] = "pipeline_definition"
    result.setdefault("relationships", [])
    result.setdefault("observation", {})
    result.setdefault("quality_defaults", {})
    result["definition_sha256"] = sha256({k: v for k, v in result.items() if k != "definition_sha256"})
    return result


class PipelineExecutor:
    def __init__(self, store: MissionDataStore, registry: SchemaRegistry,
                 connectors: Mapping[str, MissionDataConnector]):
        self.store = store
        self.registry = registry
        self.connectors = dict(connectors)

    def register_pipeline(self, definition: Mapping[str, Any], *, recorded_time: str, actor: str) -> dict[str, Any]:
        checked = validate_pipeline_definition(definition)
        if checked["connector_id"] not in self.connectors:
            raise SchemaError(f"pipeline references unknown connector: {checked['connector_id']}")
        if self.registry.get_schema(checked["schema_id"], checked["schema_version"]) is None:
            raise SchemaError(f"pipeline references unregistered schema {checked['schema_id']}@{checked['schema_version']}")
        for ref in checked["mappings"]:
            if self.registry.get_mapping(ref["mapping_id"], ref["version"]) is None:
                raise SchemaError(f"pipeline references unregistered mapping {ref['mapping_id']}@{ref['version']}")
        existing = self.get_pipeline(checked["pipeline_id"], checked["version"])
        if existing is not None:
            if existing["definition_sha256"] != checked["definition_sha256"]:
                raise SchemaError(f"pipeline {checked['pipeline_id']}@{checked['version']} already registered with different content")
            return existing
        self.store.append("PIPELINE_REGISTERED", checked, recorded_time=recorded_time, actor=actor)
        return checked

    def get_pipeline(self, pipeline_id: str, version: str | None = None) -> dict[str, Any] | None:
        matches = [d for d in self.store.records_of("pipeline_definition") if d["pipeline_id"] == pipeline_id
                   and (version is None or d["version"] == version)]
        return matches[-1] if matches else None

    def run(self, pipeline_id: str, body: bytes, *, source_id: str, source_time: str | None,
            received_time: str, recorded_time: str, actor: str,
            marking_override: Marking | None = None) -> dict[str, Any]:
        definition = self.get_pipeline(pipeline_id)
        if definition is None:
            raise SchemaError(f"pipeline not registered: {pipeline_id}")
        connector = self.connectors[definition["connector_id"]]
        marking = marking_override or marking_from_record(definition["marking"])
        outcome = connector.ingest(
            self.store, self.registry, body, source_id=source_id, received_time=received_time,
            recorded_time=recorded_time, actor=actor, marking=marking, source_time=source_time,
            schema_version=definition["schema_version"], watermark=source_watermark(self.store, source_id),
        )
        ingestion = outcome.ingestion
        result: dict[str, Any] = {
            "pipeline_id": pipeline_id, "status": outcome.status, "ingestion_id": ingestion["ingestion_id"],
            "late": ingestion["late"], "duplicate": bool(ingestion["duplicate_of"]),
            "quarantined": ingestion["quarantined"], "objects": [], "relationships": [],
            "transformations": [], "warnings": list(outcome.validation.get("warnings", [])),
        }
        if outcome.status != "ACCEPTED":
            return result
        mappings = [self.registry.get_mapping(ref["mapping_id"], ref["version"])
                    for ref in definition["mappings"]]
        for unit in self._units(definition, outcome.payload):
            self._process_unit(definition, mappings, unit, ingestion, marking, recorded_time, actor, result)
        return result

    def _units(self, definition: Mapping[str, Any], payload: Any) -> list[dict[str, Any]]:
        if definition["unit"] == "rows":
            return list(payload["rows"])
        if definition["unit"] == "features":
            return [{**(f.get("properties") or {}), "__geometry": f.get("geometry")} for f in payload["features"]]
        return [payload]

    def _process_unit(self, definition: Mapping[str, Any], mappings: list[Mapping[str, Any]],
                      unit: Mapping[str, Any], ingestion: Mapping[str, Any],
                      marking: Marking, recorded_time: str, actor: str, result: dict[str, Any]) -> None:
        config = definition["object"]
        mode = definition["mode"]
        evidence_bundle = unit if mode == "evidence_observation" else None
        if evidence_bundle is not None:
            unit = dict(evidence_bundle.get("assertion") or {})
        external_id = unit.get(config["external_id_field"])
        if external_id in (None, ""):
            result["warnings"].append(f"unit skipped: missing external id field {config['external_id_field']}")
            result.setdefault("skipped_units", 0)
            result["skipped_units"] += 1
            return
        merged: dict[str, Any] = {"attributes": {}, "quality": dict(definition.get("quality_defaults", {})),
                                  "labels": [], "warnings": [], "lossy_operations": []}
        mapping_ids: list[str] = []
        for ref, mapping in zip(definition["mappings"], mappings):
            applied = apply_mapping(mapping, unit)
            merged["attributes"].update(applied["attributes"])
            merged["quality"].update(applied["quality"])
            merged["labels"].extend(applied["labels"])
            merged["warnings"].extend(applied["warnings"])
            merged["lossy_operations"].extend(applied["lossy_operations"])
            for key in ("external_id", "valid_from", "valid_to", "source_time", "time_precision", "geometry", "epistemic_state"):
                if key in applied:
                    merged[key] = applied[key]
            mapping_ids.append(f"{ref['mapping_id']}@{ref['version']}")
        object_id = f"{config['id_prefix']}{external_id}"
        version = self.store.next_object_version(object_id)
        version_id = f"{object_id}@v{version}"
        transformation_id = digest_id("tf", "|".join(mapping_ids), ingestion["ingestion_id"], str(external_id))
        geometry = None
        geometry_source = merged.get("geometry", unit.get("__geometry"))
        if geometry_source:
            geometry = geometry_from_geojson(geometry_source)
        elif "point_fields" in config:
            lon = unit.get(config["point_fields"]["lon"])
            lat = unit.get(config["point_fields"]["lat"])
            if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
                geometry = geometry_from_geojson({"type": "Point", "coordinates": [lon, lat]})
        evidence_refs = ()
        provenance_mode = "OPERATIONAL"
        epistemic = merged.get("epistemic_state") or config.get("epistemic_default", "REPORTED")
        quality = dict(merged["quality"])
        quality.setdefault("schema_validity", "VALID")
        quality.setdefault("synchronization_state", "SYNCHRONIZED")
        entries = [e for mapping in mappings for e in mapping["entries"]]
        present = sum(1 for e in entries if unit.get(e["source_field"]) is not None)
        quality.setdefault("completeness", round(present / len(entries), 3) if entries else "UNKNOWN")
        if evidence_bundle is not None:
            evidence = evidence_ref_from_bundle(evidence_bundle)
            evidence_refs = (evidence,)
            provenance_mode = "EVIDENTIARY"
            epistemic = merged.get("epistemic_state") or "EXTRACTED"
            quality.setdefault("evidence_independence",
                               "DEPENDENT_GROUP" if evidence.dependence_group_id else "UNKNOWN")
            quality.setdefault("review_state", evidence.review_state)
        provenance = ProvenanceSummary(
            mode=provenance_mode, source_ids=(ingestion["source_id"],),
            ingestion_ids=(ingestion["ingestion_id"],), transformation_ids=(transformation_id,),
            evidence=evidence_refs,
        )
        source_time = merged.get("source_time", ingestion["source_time"])
        transformation = TransformationRecord(
            transformation_id=transformation_id, implementation_id=IMPLEMENTATION_ID,
            implementation_version=IMPLEMENTATION_VERSION,
            mapping_id="|".join(mapping_ids), mapping_version=definition["version"],
            input_refs=({"kind": "ingestion", "ref": ingestion["ingestion_id"], "sha256": ingestion["content_sha256"]},),
            output_refs=({"kind": "object_version", "ref": version_id, "sha256": None},),
            actor=actor, time=recorded_time, warnings=tuple(merged["warnings"]),
            lossy_operations=tuple(merged["lossy_operations"]),
            validation="VALID",
        )
        self.store.append("TRANSFORMATION_RECORDED", transformation, recorded_time=recorded_time, actor=actor)
        obj = ObjectVersion(
            object_id=object_id, version=version, object_type=config["object_type"],
            lifecycle=config.get("lifecycle", "ACTIVE"), labels=tuple(dict.fromkeys(merged["labels"])),
            external_refs=(ExternalRef(system=config.get("external_system", ingestion["source_id"]),
                                       external_id=str(external_id),
                                       imported_version=str(source_time or "UNKNOWN"),
                                       ingestion_id=ingestion["ingestion_id"], source_time=source_time,
                                       sync_status="SYNCHRONIZED",
                                       identity_bearing=config.get("external_refs_identity_bearing", True)),),
            valid_from=merged.get("valid_from", source_time), valid_to=merged.get("valid_to"),
            source_time=source_time, time_precision=merged.get("time_precision", "UNKNOWN"),
            recorded_time=recorded_time, geometry=geometry, attributes=merged["attributes"],
            quality=quality, epistemic_state=epistemic, marking=marking, provenance=provenance,
        )
        self.store.append("OBJECT_VERSION_APPENDED", obj, recorded_time=recorded_time, actor=actor)
        result["objects"].append(version_id)
        result["transformations"].append(transformation_id)
        rules = list(definition.get("relationships", []))
        if mode in ("observation", "evidence_observation"):
            observation = definition["observation"]
            target_value = unit.get(observation["target_field"])
            if target_value:
                prefix = observation.get("target_prefix", "")
                if "prefix_map" in observation:
                    kind_value = unit.get(observation["prefix_map_field"])
                    prefix = observation["prefix_map"].get(kind_value, prefix)
                rules.append({"relation_type": "REPORTS_ON", "target_fixed": f"{prefix}{target_value}",
                              "direction": "OUT"})
        for rule in rules:
            if rule.get("target_fixed"):
                target = rule["target_fixed"]
            else:
                value = unit.get(rule["target_field"])
                if value in (None, ""):
                    continue
                target = f"{rule.get('target_prefix', '')}{value}"
            source_object, target_object = (object_id, target) if rule.get("direction", "OUT") == "OUT" else (target, object_id)
            relationship_id = digest_id("rel", rule["relation_type"], source_object, target_object)
            relationship = RelationshipVersion(
                relationship_id=relationship_id, version=self.store.next_relationship_version(relationship_id),
                relation_type=rule["relation_type"], source_object_id=source_object, target_object_id=target_object,
                valid_from=merged.get("valid_from", source_time), valid_to=None, recorded_time=recorded_time,
                evidence_refs=(ingestion["ingestion_id"],), derivation="MAPPING",
                confidence="UNKNOWN", status="ACTIVE", marking=marking, provenance=provenance,
            )
            self.store.append("RELATIONSHIP_VERSION_APPENDED", relationship, recorded_time=recorded_time, actor=actor)
            result["relationships"].append(f"{relationship_id}@v{relationship.version}")

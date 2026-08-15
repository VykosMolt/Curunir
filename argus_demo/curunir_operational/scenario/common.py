"""Shared scenario setup so multiple scenarios reuse one fabric wiring without
duplicating ingestion, provenance or access logic."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from curunir_operational.contracts import SourceRecord
from curunir_operational.pipelines import PipelineExecutor, build_connector
from curunir_operational.schema_registry import SchemaRegistry
from curunir_operational.store import MissionDataStore
from curunir_operational.workbench import validate_workshop_definition


def register_sources(store: MissionDataStore, sources: Iterable[SourceRecord], *, recorded_time: str) -> None:
    for record in sources:
        store.append("SOURCE_REGISTERED", record, recorded_time=recorded_time, actor="fixture")


def register_bundle(store: MissionDataStore, registry: SchemaRegistry, *, schemas: Iterable[Mapping[str, Any]],
                    mappings: Iterable[Mapping[str, Any]], pipelines: Iterable[Mapping[str, Any]],
                    workshops: Iterable[Mapping[str, Any]] = (), recorded_time: str) -> PipelineExecutor:
    for schema in schemas:
        registry.register_schema(schema, recorded_time=recorded_time, actor="fixture")
    for mapping in mappings:
        registry.register_mapping(mapping, recorded_time=recorded_time, actor="fixture")
    connectors = {p["connector_id"]: build_connector(p.get("connector_kind", "json"), p["connector_id"],
                                                     p["schema_id"], p.get("event_id_field"))
                  for p in pipelines}
    executor = PipelineExecutor(store, registry, connectors)
    for pipeline in pipelines:
        executor.register_pipeline(pipeline, recorded_time=recorded_time, actor="fixture")
    for workshop in workshops:
        store.append("WORKSHOP_REGISTERED", validate_workshop_definition(workshop),
                     recorded_time=recorded_time, actor="fixture")
    return executor


def source(source_id: str, source_type: str, system: str, operator: str, created: str, marking,
           status: str = "ACTIVE") -> SourceRecord:
    return SourceRecord(source_id, source_type, system, f"{system.lower()}-01", operator, "CIVDEF-AUTH",
                        {"track_record": "UNKNOWN"}, marking, status, "", created)

"""Mission data connectors: JSON, CSV and GeoJSON feeds.

Discipline: the raw payload is persisted content-addressed BEFORE parsing or
classification; every receipt — valid, malformed, duplicate, unsupported or
late — leaves an ingestion record. Malformed and schema-invalid payloads are
quarantined, never dropped. Duplicates are detected by idempotency key and
recorded as DUPLICATE referencing the original ingestion.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .access import Marking
from .canonical import digest_id, parse_time, sha256
from .contracts import IngestionEvent
from .schema_registry import SchemaRegistry
from .store import MissionDataStore


@dataclass(frozen=True)
class ConnectorOutcome:
    status: str  # ACCEPTED | QUARANTINED | DUPLICATE
    ingestion: dict[str, Any]
    payload: Any | None
    validation: dict[str, Any]


class MissionDataConnector:
    connector_id = ""
    connector_version = "1.0"
    media_type = ""
    schema_id = ""

    def __init__(self, connector_id: str, schema_id: str, event_id_field: str | None = None):
        self.connector_id = connector_id
        self.schema_id = schema_id
        self.event_id_field = event_id_field

    def parse(self, body: bytes) -> Any:
        raise NotImplementedError

    def normalize(self, payload: Any, schema: Mapping[str, Any] | None) -> Any:
        return payload

    def idempotency_key(self, source_id: str, body_sha256: str, payload: Any | None) -> str:
        if payload is not None and self.event_id_field and isinstance(payload, Mapping) \
                and payload.get(self.event_id_field) is not None:
            event_id = payload[self.event_id_field]
            if isinstance(event_id, str):
                # a lone surrogate in a source event_id would crash this sha256's
                # canonical encode (it runs OUTSIDE the parse try) and lose the
                # document; scrub it, matching the fabric connector edge (NEW-A4)
                event_id = event_id.encode("utf-8", "replace").decode("utf-8")
            return sha256({"connector": self.connector_id, "source": source_id,
                           "event_id": event_id})
        return sha256({"connector": self.connector_id, "source": source_id, "content": body_sha256})

    def ingest(self, store: MissionDataStore, registry: SchemaRegistry, body: bytes, *,
               source_id: str, received_time: str, recorded_time: str, actor: str, marking: Marking,
               source_time: str | None = None, schema_version: str = "1.0",
               watermark: str | None = None) -> ConnectorOutcome:
        body_sha = store.put_payload(body)  # acquisition custody first, before any parsing
        payload: Any | None = None
        parse_error: str | None = None
        try:
            payload = self.parse(body)
        except Exception as exc:
            parse_error = f"MALFORMED_PAYLOAD: {exc}"
        if parse_error is None:
            schema = registry.get_schema(self.schema_id, schema_version)
            payload = self.normalize(payload, schema)
            validation = registry.validate_payload(self.schema_id, schema_version, payload)
        else:
            validation = {"status": "INVALID", "schema_version": schema_version, "errors": [parse_error], "warnings": []}
        key = self.idempotency_key(source_id, body_sha, payload if parse_error is None else None)
        original = store.find_ingestion_by_key(key)
        status = validation["status"]
        duplicate_of = None
        if original is not None:
            status = "DUPLICATE"
            duplicate_of = original
        quarantined = status in ("INVALID", "UNSUPPORTED_SCHEMA_VERSION")
        late = bool(source_time and watermark and parse_time(source_time) < parse_time(watermark))
        ingestion = IngestionEvent(
            ingestion_id=digest_id("ing", self.connector_id, source_id, key, received_time),
            connector_id=self.connector_id, connector_version=self.connector_version,
            source_id=source_id, source_time=source_time, received_time=received_time,
            content_sha256=body_sha, schema_id=self.schema_id,
            schema_version=validation.get("schema_version", schema_version),
            idempotency_key=key, validation=status,
            quarantined=quarantined, quarantine_reasons=tuple(validation["errors"]) if quarantined else (),
            duplicate_of=duplicate_of, late=late, payload_ref=body_sha, marking=marking,
        )
        store.append("INGESTION_RECORDED", ingestion, recorded_time=recorded_time, actor=actor)
        outcome_status = "DUPLICATE" if duplicate_of else ("QUARANTINED" if quarantined else "ACCEPTED")
        return ConnectorOutcome(outcome_status, ingestion.to_record(),
                                payload if outcome_status == "ACCEPTED" else None, validation)


class JsonFeedConnector(MissionDataConnector):
    connector_version = "1.0"
    media_type = "application/json"

    def parse(self, body: bytes) -> Any:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")
        return payload


class CsvFeedConnector(MissionDataConnector):
    connector_version = "1.0"
    media_type = "text/csv"

    def parse(self, body: bytes) -> Any:
        text = body.decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise ValueError("CSV payload has no data rows")
        if any(None in row or None in row.values() for row in rows):
            raise ValueError("CSV row width does not match header")
        return {"rows": rows}

    def normalize(self, payload: Any, schema: Mapping[str, Any] | None) -> Any:
        if schema is None:
            return payload
        fields = schema["fields"]
        typed_rows = []
        for row in payload["rows"]:
            typed: dict[str, Any] = {}
            for name, value in row.items():
                spec = fields.get(name)
                if spec is None or value == "":
                    typed[name] = value if value != "" else None
                    continue
                try:
                    if spec["type"] == "number":
                        typed[name] = float(value)
                    elif spec["type"] == "integer":
                        typed[name] = int(value)
                    elif spec["type"] == "boolean":
                        typed[name] = value.strip().lower() in ("true", "1", "yes")
                    else:
                        typed[name] = value
                except ValueError:
                    typed[name] = value  # leave for the validator to report
            typed_rows.append(typed)
        return {"rows": typed_rows, **{k: v for k, v in payload.items() if k != "rows"}}


class GeoJsonConnector(MissionDataConnector):
    connector_version = "1.0"
    media_type = "application/geo+json"

    def parse(self, body: bytes) -> Any:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
            raise ValueError("payload must be a GeoJSON FeatureCollection")
        return payload


def source_watermark(store: MissionDataStore, source_id: str) -> str | None:
    return store.source_watermark(source_id)

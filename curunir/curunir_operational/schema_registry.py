"""Schema registry and explicit mapping contracts.

Schemas and mappings are inspectable records in the event log. Validation is a
bounded engine over required fields, types, formats and enums, and runs no
code from configuration. A mapping declares every field route, transform,
default, ignored field and lossy operation, and unlisted keys are rejected.
"""
from __future__ import annotations

from typing import Any, Mapping

from .canonical import require_aware, sha256
from .contracts import OBJECT_TYPES, QUALITY_DIMENSIONS
from .geometry import geometry_from_geojson
from .store import MissionDataStore

FIELD_TYPES = ("string", "number", "integer", "boolean", "object", "array")
FIELD_FORMATS = ("iso-datetime", "sha256", "geojson-geometry")
PAYLOAD_KINDS = ("document", "rows", "geojson")
TRANSFORMS = ("identity", "to_number", "to_string", "iso_time", "boolean", "naive_utc")
MAPPING_ENTRY_KEYS = {"source_field", "target", "transform", "default", "on_missing"}
ON_MISSING = ("UNKNOWN", "ERROR", "SKIP")
MAPPING_TARGET_PREFIXES = ("attributes.", "quality.")
MAPPING_TARGETS = ("external_id", "label", "valid_from", "valid_to", "source_time", "time_precision", "geometry", "epistemic_state")


class SchemaError(ValueError):
    pass


def _definition_hash(definition: Mapping[str, Any]) -> str:
    return sha256({k: v for k, v in definition.items() if k != "definition_sha256"})


def validate_schema_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"record_type", "schema_id", "version", "media_type", "payload_kind", "compatible_with",
               "fields", "description", "source_schema", "definition_sha256"}
    unknown = set(definition) - allowed
    if unknown:
        raise SchemaError(f"unknown schema definition keys: {sorted(unknown)}")
    for key in ("schema_id", "version", "media_type", "payload_kind", "fields"):
        if key not in definition:
            raise SchemaError(f"schema definition missing {key}")
    if definition["payload_kind"] not in PAYLOAD_KINDS:
        raise SchemaError(f"unknown payload kind: {definition['payload_kind']}")
    for name, spec in definition["fields"].items():
        extra = set(spec) - {"type", "required", "format", "enum", "description"}
        if extra:
            raise SchemaError(f"field {name}: unknown keys {sorted(extra)}")
        if spec.get("type") not in FIELD_TYPES:
            raise SchemaError(f"field {name}: unknown type {spec.get('type')!r}")
        if "format" in spec and spec["format"] not in FIELD_FORMATS:
            raise SchemaError(f"field {name}: unknown format {spec['format']!r}")
    result = dict(definition)
    result["record_type"] = "schema_definition"
    result.setdefault("compatible_with", [])
    result.setdefault("description", "")
    result.setdefault("source_schema", None)
    result["definition_sha256"] = _definition_hash(result)
    return result


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def _check_value(name: str, spec: Mapping[str, Any], value: Any, errors: list[str]) -> None:
    kind = spec["type"]
    ok = _TYPE_CHECKS[kind](value)
    if not ok:
        errors.append(f"{name}: expected {kind}")
        return
    if spec.get("format") == "iso-datetime":
        try:
            require_aware(value)
        except Exception:
            errors.append(f"{name}: not a timezone-aware ISO datetime")
    if spec.get("format") == "sha256" and not (isinstance(value, str) and len(value) == 64):
        errors.append(f"{name}: not a sha256")
    if spec.get("format") == "geojson-geometry":
        try:
            geometry_from_geojson(value)
        except Exception as exc:
            errors.append(f"{name}: invalid geometry ({exc})")
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{name}: {value!r} not in enum")


def validate_document(fields: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, Mapping):
        return ["payload is not an object"], warnings
    for name, spec in fields.items():
        if name not in payload or payload[name] is None:
            if spec.get("required"):
                errors.append(f"{name}: required field missing")
            continue
        _check_value(name, spec, payload[name], errors)
    for name in payload:
        if name not in fields and name != "schema_version":
            warnings.append(f"undeclared field present: {name}")
    return errors, warnings


class SchemaRegistry:
    def __init__(self, store: MissionDataStore):
        self.store = store

    def _definitions(self, record_type: str) -> list[dict[str, Any]]:
        return self.store.records_of(record_type)

    def register_schema(self, definition: Mapping[str, Any], *, recorded_time: str, actor: str) -> dict[str, Any]:
        checked = validate_schema_definition(definition)
        existing = self.get_schema(checked["schema_id"], checked["version"])
        if existing is not None:
            if existing["definition_sha256"] != checked["definition_sha256"]:
                raise SchemaError(f"schema {checked['schema_id']}@{checked['version']} already registered with different content")
            return existing
        self.store.append("SCHEMA_REGISTERED", checked, recorded_time=recorded_time, actor=actor)
        return checked

    def get_schema(self, schema_id: str, version: str | None = None) -> dict[str, Any] | None:
        matches = [d for d in self._definitions("schema_definition") if d["schema_id"] == schema_id
                   and (version is None or d["version"] == version)]
        return matches[-1] if matches else None

    def schema_versions(self, schema_id: str) -> list[str]:
        return [d["version"] for d in self._definitions("schema_definition") if d["schema_id"] == schema_id]

    def validate_payload(self, schema_id: str, version: str, payload: Any) -> dict[str, Any]:
        declared = payload.get("schema_version") if isinstance(payload, Mapping) else None
        effective = declared or version
        definition = self.get_schema(schema_id, effective)
        if definition is None:
            known = self.schema_versions(schema_id)
            compatible = self.get_schema(schema_id, version)
            if compatible and effective in compatible.get("compatible_with", []):
                definition = compatible
            else:
                return {"status": "UNSUPPORTED_SCHEMA_VERSION", "schema_version": effective,
                        "errors": [f"schema {schema_id}@{effective} not registered (known: {known})"], "warnings": []}
        kind = definition["payload_kind"]
        if kind == "rows":
            if not isinstance(payload, Mapping) or not isinstance(payload.get("rows"), list):
                return {"status": "INVALID", "schema_version": effective, "errors": ["payload must contain rows"], "warnings": []}
            errors: list[str] = []
            warnings: list[str] = []
            for index, row in enumerate(payload["rows"]):
                row_errors, row_warnings = validate_document(definition["fields"], row)
                errors.extend(f"row {index}: {e}" for e in row_errors)
                warnings.extend(f"row {index}: {w}" for w in row_warnings)
        elif kind == "geojson":
            if not isinstance(payload, Mapping) or payload.get("type") != "FeatureCollection":
                return {"status": "INVALID", "schema_version": effective, "errors": ["payload must be a GeoJSON FeatureCollection"], "warnings": []}
            errors = []
            warnings = []
            for index, feature in enumerate(payload.get("features", [])):
                if not isinstance(feature, Mapping):
                    errors.append(f"feature {index}: feature is not an object")
                    continue
                try:
                    geometry_from_geojson(feature.get("geometry") or {})
                except Exception as exc:
                    errors.append(f"feature {index}: invalid geometry ({exc})")
                feature_errors, feature_warnings = validate_document(definition["fields"], feature.get("properties") or {})
                errors.extend(f"feature {index}: {e}" for e in feature_errors)
                warnings.extend(f"feature {index}: {w}" for w in feature_warnings)
        else:
            if not isinstance(payload, Mapping):
                return {"status": "INVALID", "schema_version": effective, "errors": ["payload must be an object"], "warnings": []}
            errors, warnings = validate_document(definition["fields"], payload)
        return {"status": "INVALID" if errors else "VALID", "schema_version": definition["version"],
                "errors": errors, "warnings": warnings}

    def register_mapping(self, mapping: Mapping[str, Any], *, recorded_time: str, actor: str) -> dict[str, Any]:
        checked = validate_mapping_definition(mapping)
        if self.get_schema(checked["input_schema_id"], checked["input_schema_version"]) is None:
            raise SchemaError(f"mapping references unregistered schema {checked['input_schema_id']}@{checked['input_schema_version']}")
        existing = self.get_mapping(checked["mapping_id"], checked["version"])
        if existing is not None:
            if existing["definition_sha256"] != checked["definition_sha256"]:
                raise SchemaError(f"mapping {checked['mapping_id']}@{checked['version']} already registered with different content")
            return existing
        self.store.append("MAPPING_REGISTERED", checked, recorded_time=recorded_time, actor=actor)
        return checked

    def get_mapping(self, mapping_id: str, version: str | None = None) -> dict[str, Any] | None:
        matches = [d for d in self._definitions("mapping_definition") if d["mapping_id"] == mapping_id
                   and (version is None or d["version"] == version)]
        return matches[-1] if matches else None

    def export_definitions(self) -> dict[str, Any]:
        return {"schemas": self._definitions("schema_definition"), "mappings": self._definitions("mapping_definition")}


def validate_mapping_definition(mapping: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"record_type", "mapping_id", "version", "input_schema_id", "input_schema_version",
               "output_object_type", "entries", "ignored_fields", "lossy_operations", "quality_effects",
               "implementation_version", "definition_sha256"}
    unknown = set(mapping) - allowed
    if unknown:
        raise SchemaError(f"unknown mapping keys: {sorted(unknown)}")
    for key in ("mapping_id", "version", "input_schema_id", "input_schema_version", "output_object_type", "entries"):
        if key not in mapping:
            raise SchemaError(f"mapping missing {key}")
    if mapping["output_object_type"] not in OBJECT_TYPES:
        raise SchemaError(f"unknown output object type: {mapping['output_object_type']}")
    for entry in mapping["entries"]:
        extra = set(entry) - MAPPING_ENTRY_KEYS
        if extra:
            raise SchemaError(f"mapping entry has hidden keys: {sorted(extra)}")
        if "source_field" not in entry or "target" not in entry:
            raise SchemaError("mapping entry needs source_field and target")
        target = entry["target"]
        if target not in MAPPING_TARGETS and not target.startswith(MAPPING_TARGET_PREFIXES):
            raise SchemaError(f"unknown mapping target: {target}")
        if entry.get("transform", "identity") not in TRANSFORMS:
            raise SchemaError(f"transform not in allowlist: {entry.get('transform')}")
        if entry.get("on_missing", "SKIP") not in ON_MISSING:
            raise SchemaError(f"unknown on_missing policy: {entry.get('on_missing')}")
    for dim in mapping.get("quality_effects", {}):
        if dim not in QUALITY_DIMENSIONS:
            raise SchemaError(f"unknown quality dimension in mapping: {dim}")
    result = dict(mapping)
    result["record_type"] = "mapping_definition"
    result.setdefault("ignored_fields", [])
    result.setdefault("lossy_operations", [])
    result.setdefault("quality_effects", {})
    result.setdefault("implementation_version", "curunir-operational-mapping-engine-v1")
    result["definition_sha256"] = _definition_hash(result)
    return result


def _transform(name: str, value: Any) -> Any:
    if name == "identity":
        return value
    if name == "to_number":
        return float(value)
    if name == "to_string":
        return str(value)
    if name == "iso_time":
        require_aware(value)
        return value
    if name == "boolean":
        return bool(value)
    if name == "naive_utc":
        # Public feeds often send a naive timestamp; read it as UTC. A value
        # that already carries an offset is left alone.
        from datetime import datetime
        text = str(value)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            require_aware(text.replace("Z", "+00:00"))
            return text.replace("Z", "+00:00")
        coerced = text + "+00:00"
        require_aware(coerced)
        return coerced
    raise SchemaError(f"transform not in allowlist: {name}")


def apply_mapping(mapping: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Apply one registered mapping to one document or row, with an audit of
    warnings and lossy operations."""
    out: dict[str, Any] = {"attributes": {}, "quality": dict(mapping.get("quality_effects", {})), "labels": []}
    warnings: list[str] = []
    for entry in mapping["entries"]:
        source_field = entry["source_field"]
        target = entry["target"]
        if source_field in payload and payload[source_field] is not None:
            try:
                value = _transform(entry.get("transform", "identity"), payload[source_field])
            except Exception as exc:
                warnings.append(f"{source_field}: transform failed ({exc})")
                continue
        elif "default" in entry:
            value = entry["default"]
            warnings.append(f"{source_field}: declared default applied")
        else:
            policy = entry.get("on_missing", "SKIP")
            if policy == "ERROR":
                raise SchemaError(f"required mapped field missing: {source_field}")
            if policy == "UNKNOWN" and target.startswith("attributes."):
                out["attributes"][target.split(".", 1)[1]] = "UNKNOWN"
            continue
        if target.startswith("attributes."):
            out["attributes"][target.split(".", 1)[1]] = value
        elif target.startswith("quality."):
            out["quality"][target.split(".", 1)[1]] = value
        elif target == "label":
            out["labels"].append(str(value))
        else:
            out[target] = value
    mapped_fields = {e["source_field"] for e in mapping["entries"]}
    ignored = [f for f in payload if f not in mapped_fields
               and f not in mapping.get("ignored_fields", []) and f != "schema_version"]
    if ignored:
        warnings.append(f"unmapped fields dropped: {sorted(ignored)}")
    out["warnings"] = warnings
    out["lossy_operations"] = list(mapping.get("lossy_operations", []))
    return out

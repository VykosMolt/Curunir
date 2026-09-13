"""Information-flow policy for the whole record graph.

Which fields name other records is read from the record classes' annotations
(see references.py). Marking admission and the control walks both use that
one reading, so a field that names a record cannot be forgotten in a table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .access import Marking, inherited_marking, marking_from_record
from .contracts import RECORD_CLASSES, REGISTRY_GENERATION
from .references import FieldSpec, RefTo, field_specs


_CONTRACT_MODULES = ("curunir_fabric.contracts", "curunir_semantic.contracts", "curunir_identity.contracts",
                     "curunir_analytic.contracts", "curunir_workbench.contracts")
_registered = False


def _register_product_contracts() -> None:
    """Import the product's contract modules once, so every record class is registered.

    An import failure propagates: a policy built from half the classes would
    fail open, so it is never built.
    """
    global _registered
    if _registered:
        return
    import importlib
    for name in _CONTRACT_MODULES:
        importlib.import_module(name)
    _registered = True


def record_class(record_type: str) -> type | None:
    """The registered class for a record type."""
    cls = RECORD_CLASSES.get(record_type)
    if cls is None and record_type not in UNTYPED_RECORD_TYPES:
        _register_product_contracts()
        cls = RECORD_CLASSES.get(record_type)
    return cls


class _PrimaryIdFields(Mapping[str, str]):
    """record_type -> the field that holds its id, read from the record classes."""

    def __getitem__(self, record_type: str) -> str:
        cls = record_class(record_type)
        if cls is None or not cls.ID_FIELD:
            raise KeyError(record_type)
        return cls.ID_FIELD

    def __iter__(self):
        _register_product_contracts()
        return iter([name for name, cls in RECORD_CLASSES.items() if cls.ID_FIELD])

    def __len__(self) -> int:
        return sum(1 for _ in self)


PRIMARY_ID_FIELDS: Mapping[str, str] = _PrimaryIdFields()


KIND_ALIASES = {
    "claim": "semantic_claim",
    "observation": "semantic_observation",
    "manifestation": "fabric_manifestation",
    "document": "semantic_document",
    "change": "semantic_change",
    "theme": "analytic_theme",
    "narrative": "analytic_narrative",
    "forecast": "analytic_forecast",
    "indicator": "forecast_indicator",
    "warning": "strategic_warning",
    "report": "workbench_report",
    "report_sentence": "workbench_report",
    "report_section": "workbench_report",
    "annotation": "workbench_annotation",
    "objective": "mission_objective",
    "assumption": "analytic_assumption",
    "path": "impact_path",
    "option": "response_option",
    "episode": "historical_episode",
    "analogue": "historical_analogue",
    "relationship": "relationship_version",
    "object": "object_version",
    "requirement": "information_requirement",
}


REFERENCE_FIELD_SUFFIXES = ("_id", "_ids", "_ref", "_refs")

# Stored families that are free-form mappings rather than record classes.
UNTYPED_RECORD_TYPES = frozenset({
    "schema_definition", "mapping_definition", "pipeline_definition",
    "workshop_definition", "fabric_source_descriptor",
})


def reference_field_is_classified(record_type: str, field_name: str) -> bool:
    """Whether a field that could carry ids is annotated as a reference, a label,
    a nested record, or the record's own id. Id-shaped names and tuples of
    string pairs count as able to carry ids."""
    cls = record_class(record_type)
    spec = field_specs(cls).get(field_name) if cls is not None else None
    shaped = field_name.endswith(REFERENCE_FIELD_SUFFIXES) or (spec is not None and spec.pairs_shaped)
    if not shaped:
        return True
    if cls is None:
        return False
    if field_name == cls.ID_FIELD:
        return True
    return spec is not None and (spec.ref is not None or spec.label or spec.nested is not None)


@dataclass(frozen=True, order=True)
class MaterialReference:
    kind: str
    record_id: str


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value:
            yield value
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            if isinstance(item, str) and item:
                yield item


def _normalize_kind(kind: str) -> str:
    return KIND_ALIASES.get(kind.lower(), kind.lower())


def _pair_references(value: Any) -> Iterable[MaterialReference]:
    """References from (kind, id) tuples, {"kind", "ref"|"id"} mappings, or bare ids."""
    for pair in value or ():
        if isinstance(pair, (list, tuple)) and len(pair) == 2 \
                and isinstance(pair[0], str) and isinstance(pair[1], str):
            yield MaterialReference(KIND_ALIASES.get(pair[0], pair[0]), pair[1])
        elif isinstance(pair, Mapping):
            kind = pair.get("kind")
            ref = pair.get("ref") or pair.get("id")
            if isinstance(kind, str) and isinstance(ref, str) and ref:
                yield MaterialReference(_normalize_kind(kind), ref)
        elif isinstance(pair, str) and pair:
            yield MaterialReference("*", pair)


def _field_references(spec: RefTo, value: Any, owner: Mapping[str, Any]) -> Iterable[MaterialReference]:
    if spec.pairs:
        if spec.slot is None:
            yield from _pair_references(value)
        else:
            for pair in value or ():
                if isinstance(pair, (list, tuple)) and len(pair) > spec.slot and isinstance(pair[spec.slot], str) \
                        and pair[spec.slot]:
                    yield MaterialReference(spec.kind, pair[spec.slot])
        # A pair written as a mapping may carry more than its kind and id.
        for item in value or ():
            if isinstance(item, Mapping):
                yield from _blob_references(item, frozenset())
        return
    if spec.kind_from:
        kind = owner.get(spec.kind_from)
        if not isinstance(kind, str):
            return
        kind = _normalize_kind(kind)
    else:
        kind = spec.kind
    for reference in _strings(value):
        yield MaterialReference(kind, reference)


_BLOB_POLICY: tuple[dict[str, RefTo], tuple[tuple[str, str], ...], int] | None = None


def _blob_policy() -> tuple[dict[str, RefTo], tuple[tuple[str, str], ...]]:
    """Field names that mean a reference anywhere inside an unstructured mapping.

    Derived from every record class, so a name means the same thing in a
    free-form payload as on a typed record. Joining too much is safe, missing a
    reference is not.
    """
    global _BLOB_POLICY
    _register_product_contracts()
    if _BLOB_POLICY is None or _BLOB_POLICY[2] != REGISTRY_GENERATION[0]:
        fields: dict[str, RefTo] = {}
        pairs: set[tuple[str, str]] = set()
        for cls in RECORD_CLASSES.values():
            for name, spec in field_specs(cls).items():
                if spec.ref is None:
                    continue
                if spec.ref.kind_from:
                    pairs.add((spec.ref.kind_from, name))
                elif name in fields and fields[name] != spec.ref:
                    fields[name] = RefTo("*")
                else:
                    fields.setdefault(name, spec.ref)
        # A record's own id field, seen inside someone else's payload, names that record.
        for record_type, cls in RECORD_CLASSES.items():
            name = cls.ID_FIELD
            if not name:
                continue
            if name in fields and fields[name] != RefTo(record_type):
                fields[name] = RefTo("*")
            else:
                fields.setdefault(name, RefTo(record_type))
        _BLOB_POLICY = (fields, tuple(sorted(pairs)), REGISTRY_GENERATION[0])
    return _BLOB_POLICY[0], _BLOB_POLICY[1]


def _blob_references(value: Any, skip: frozenset[str]) -> Iterable[MaterialReference]:
    fields, pairs = _blob_policy()
    if isinstance(value, Mapping):
        for kind_field, id_field in pairs:
            kind, record_id = value.get(kind_field), value.get(id_field)
            if isinstance(kind, str) and isinstance(record_id, str) and record_id:
                yield MaterialReference(_normalize_kind(kind), record_id)
        for key, item in value.items():
            if key in skip:
                continue
            spec = fields.get(key)
            if spec is not None:
                yield from _field_references(spec, item, value)
            yield from _blob_references(item, skip)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _blob_references(item, skip)


def _plain(spec: FieldSpec) -> bool:
    """A declared field with no reference, label or nested-record annotation."""
    return spec.ref is None and spec.nested is None and not spec.label


def material_references(record: Mapping[str, Any]) -> tuple[MaterialReference, ...]:
    """Every record this record names, read from its class's typed fields."""
    record_type = str(record.get("record_type", ""))
    _register_product_contracts()
    cls = record_class(record_type)
    if cls is None:
        # Free-form families (schema, mapping, pipeline and workshop definitions,
        # fabric source descriptors) have no record class: scan them by field name.
        if record_type not in UNTYPED_RECORD_TYPES:
            raise ValueError(f"no record class is registered for {record_type!r}")
        return tuple(sorted(set(_blob_references(record, frozenset({"marking", "record_type"})))))
    found: set[MaterialReference] = set()

    def walk(value: Mapping[str, Any], owner_class: type) -> None:
        specs = field_specs(owner_class)
        skip = frozenset({"marking", "record_type", owner_class.ID_FIELD})
        for name, spec in specs.items():
            item = value.get(name)
            if item is None or name in skip:
                continue
            if spec.ref is not None:
                found.update(_field_references(spec.ref, item, value))
            elif spec.nested is not None:
                nested = item if isinstance(item, (list, tuple)) else (item,)
                for entry in nested:
                    if isinstance(entry, Mapping):
                        walk(entry, spec.nested)
        # A key the class does not declare, or declares without a reference
        # annotation, is scanned by name so nothing can hide a reference.
        extra = {key: item for key, item in value.items()
                 if key not in skip and (key not in specs or _plain(specs[key]))}
        if extra:
            found.update(_blob_references(extra, skip))

    walk(record, cls)
    return tuple(sorted(found))


def _report_contains(record: Mapping[str, Any], record_id: str) -> bool:
    for section in record.get("sections", ()) or ():
        if section.get("section_id") == record_id:
            return True
        if any(sentence.get("sentence_id") == record_id
               for sentence in section.get("sentences", ()) or ()):
            return True
    return False


def _by_id(store: Any, record_type: str, id_field: str, base_id: str) -> list[dict]:
    """Records of one family carrying one id, in log order."""
    index = getattr(store, "_records_by_id", None)
    if index is not None and PRIMARY_ID_FIELDS.get(record_type) == id_field:
        return index.get(record_type, {}).get(base_id, [])
    return [record for record in store.records_of(record_type)
            if record.get(id_field) == base_id]


def _latest(store: Any, record_type: str, id_field: str, record_id: str) -> dict | None:
    version: int | None = None
    base_id = record_id
    if "@v" in record_id:
        candidate, separator, suffix = record_id.rpartition("@v")
        if separator and suffix.isdigit():
            base_id = candidate
            version = int(suffix)
    matches = [
        record for record in _by_id(store, record_type, id_field, base_id)
        if version is None or record.get("version") == version
    ]
    if not matches and record_type == "workbench_report":
        matches = [
            record for record in store.records_of(record_type)
            if _report_contains(record, record_id)
        ]
    if not matches:
        return None
    return max(matches, key=lambda record: record.get("version", 1))


def resolve_reference_records(
    store: Any,
    references: Iterable[MaterialReference],
) -> tuple[dict, ...]:
    """Resolve references against current raw state, never a projection.

    An id that resolves to no record contributes no marking, so the order of
    appends decides what a dangling reference floors. A record that exists is
    always found and always joined.
    """
    resolved: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        kinds = PRIMARY_ID_FIELDS if reference.kind == "*" else {
            KIND_ALIASES.get(reference.kind, reference.kind):
            PRIMARY_ID_FIELDS.get(KIND_ALIASES.get(reference.kind, reference.kind), "")
        }
        for kind, id_field in kinds.items():
            if not id_field:
                continue
            candidate_ids = [reference.record_id]
            if kind == "object_version":
                try:
                    from curunir_semantic.worldmodel import world_object_id
                    mapped = world_object_id(reference.record_id)
                    if mapped and mapped not in candidate_ids:
                        candidate_ids.append(mapped)
                except (ImportError, TypeError, ValueError):
                    pass
            for candidate_id in candidate_ids:
                record = _latest(store, kind, id_field, candidate_id)
                key = (kind, candidate_id)
                if record is not None and key not in seen:
                    resolved.append(record)
                    seen.add(key)
    return tuple(resolved)


def resolve_reference_markings(
    store: Any,
    references: Iterable[MaterialReference | str],
) -> list[Mapping[str, Any]]:
    normalized = [
        reference if isinstance(reference, MaterialReference)
        else MaterialReference("*", reference)
        for reference in references if reference
    ]
    return [record["marking"] for record in resolve_reference_records(store, normalized)
            if isinstance(record.get("marking"), Mapping)]


def previous_version(store: Any, record: Mapping[str, Any]) -> dict | None:
    record_type = str(record.get("record_type", ""))
    id_field = PRIMARY_ID_FIELDS.get(record_type)
    if not id_field or not isinstance(record.get(id_field), str):
        return None
    return _latest(store, record_type, id_field, record[id_field])


def admit_marking(store: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy whose marking covers the prior version and every record
    it references."""
    data = dict(record)
    raw_marking = data.get("marking")
    if not isinstance(raw_marking, Mapping):
        return data
    base = marking_from_record(raw_marking)
    markings: list[Marking | Mapping[str, Any] | None] = []
    prior = previous_version(store, data)
    if prior is not None:
        markings.append(prior.get("marking"))
    markings.extend(resolve_reference_markings(store, material_references(data)))
    data["marking"] = inherited_marking(base, markings).to_record()
    return data

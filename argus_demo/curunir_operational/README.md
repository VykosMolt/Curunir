# Curunír operational plane — mission data fabric and workbench (V1)

Research-shadow implementation of `CURUNIR_MISSION_DATA_FABRIC_AND_WORKBENCH_V1`.

**Status:** `FUNCTIONAL_RESEARCH_SHADOW_VERTICAL_SLICE` — a coherent mission-data
workflow exists and runs end to end in a synthetic environment. It is **not**
production-ready, not accredited, not validated intelligence, and not a system
of record. See `docs/curunir-operational-current-state.md` for exact limitations.

## Purpose

Transforms heterogeneous synthetic inputs (JSON / CSV / GeoJSON feeds and ARGUS
Source Intelligence evidence bundles) into versioned operational objects with
preserved provenance, visible uncertainty, access compartments, a configurable
logistics workbench, evidence-bound alerts and recommendations, recorded human
decisions, and deterministic export / import / replay.

## Boundary

`SYSTEM_OF_ENGAGEMENT_AND_ANALYSIS`. Every imported identifier keeps its
originating system, external identifier, imported version, ingestion id and
sync status; authoritative records remain with the originating systems. No
canonical ARGUS PostgreSQL path exists in this package (enforced by
`tests/test_operational_protection.py`), no network access, no external action
execution, and providers cannot record human decisions.

## Quick start

```bash
cd argus_demo
# full synthetic scenario (fixture clock; deterministic)
.venv/bin/python -m curunir_operational.cli run-scenario --store /tmp/vessia/store --out /tmp/vessia/out
# access-filtered projection / report / explanation
.venv/bin/python -m curunir_operational.cli report --store /tmp/vessia/store --context ctx.json --format text
.venv/bin/python -m curunir_operational.cli explain --store /tmp/vessia/store --context ctx.json --id infra-BR-7 --markdown
# integrity, export, replay
.venv/bin/python -m curunir_operational.cli verify --store /tmp/vessia/store
.venv/bin/python -m curunir_operational.cli export --store /tmp/vessia/store --out /tmp/vessia/export
.venv/bin/python -m curunir_operational.cli replay --store /tmp/vessia/store --export /tmp/vessia/export \
    --fresh /tmp/vessia/fresh --context ctx.json --snapshot-time 2026-03-02T15:00:00+00:00
```

`ctx.json` is an access context: `{"context_id": ..., "actor_id": ..., "actor_kind":
"HUMAN"|"SERVICE", "roles": [...], "compartments": [...], "releasability": [...],
"organisation": ...}`.

## Package structure

| Module | Responsibility |
|---|---|
| `canonical` | single reuse seam: canonical bytes/hash/id/timestamp helpers |
| `contracts` | operational record ontology (validated frozen dataclasses) |
| `access`, `geometry` | fail-closed markings/contexts; WGS84 geometry + deterministic distances |
| `store` | append-only hash-chained JSONL event log, payload custody, export/import |
| `schema_registry` | schema + mapping registration, versioning, declarative validation |
| `connectors` | JSON/CSV/GeoJSON connectors (custody before parsing; quarantine; dedup; late) |
| `argus_adapter` | evidentiary-provenance adapter for Source Intelligence bundles |
| `pipelines` | configuration-driven feed → objects/relationships/lineage execution |
| `association` | conservative, reversible object association |
| `projection` | current/as-of state, freshness, clusters, access-filtered views |
| `analytics` | model plane: rule + mock providers, inference records, proposals |
| `workflow` | alerts → recommendations → human decisions (frozen evidence snapshots) |
| `workbench` | validated workshop definitions, view rendering, self-contained HTML COP |
| `sitrep` | evidence-bound situation reports (JSON/Markdown/plain text) |
| `explain` | provenance/quality/history explanation traversal |
| `sovereignty` | sovereignty manifest, open-export exit test, PACE bundle |
| `cli` | thin command layer |
| `scenario/` | synthetic Vessia Corridor fixtures + runner (never imported by core) |

## Relationship to ARGUS

ARGUS remains the evidence and research foundation. This package reuses its
canonical hashing helpers (via `canonical.py` only), adapts its Source
Intelligence evidence semantics through `argus_adapter`, and references its
process-graph identity discipline. It writes nothing into ARGUS stores and
opens no human-review gates. See `artifacts/curunir_mission_data_fabric_v1_20260720/reuse_map.md`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -k operational -q   # all no_db; PostgreSQL never contacted
```

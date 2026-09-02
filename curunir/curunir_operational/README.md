# Curunír operational plane — the trusted core

The store, the access lattice, the record ontology, and the canonical byte
contract that every other `curunir_*` package builds on. Rebuilt under
`CURUNIR_V6_7_ARCHITECTURE.md`; exercised end to end by the V6.8 human pilot.

**Status:** `FUNCTIONAL` — a coherent mission workflow runs end to end, was
driven by real operators in the V6.8 pilot, and reproduces under offline
replay. It is **not** accredited, not validated intelligence, and not a system
of record. `../CURUNIR_V6_8_QUALIFICATION.json` states exactly what was and
was not established.

> **History.** This package once also carried the `v3`..`v5_8_1` research
> campaign tree. That was removed in the V6.9 excision
> (`../CURUNIR_V6_9_EXCISION.json`); it survives at the git tag
> `archive/curunir-campaign-tree-v5x`. Nothing below depends on it.

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
cd curunir
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

`pace` refuses an output directory that already exists and publishes the bundle owner-only,
like `export`.

## Package structure

| Module | Responsibility |
|---|---|
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
| `security` | material-reference registry, reference closure, marking admission |
| `canonical` | strict canonical values: the single persisted byte contract |
| `missions` | mission and objective records |
| `geometry` | WGS84 geometry and deterministic distances |
| `delta` | delta bundles: preflight, staged install, conflict semantics |
| `partition_custody` | custody across store partitions |
| `xml_safety` | bounded, hostile-input-safe XML handling |
| `stress` | deterministic load and integrity stress runs |
| `providers_eval` | provider comparison harness (no analytical provider wired yet) |
| `cli` | thin command layer |
| `scenario/` | synthetic Vessia Corridor fixtures + runner (never imported by core) |

## Relationship to ARGUS

ARGUS remains the evidence and research foundation. This package reuses its
canonical hashing helpers (via `canonical.py` only), adapts its Source
Intelligence evidence semantics through `argus_adapter`, and references its
process-graph identity discipline. It writes nothing into ARGUS stores and
opens no human-review gates.

## Tests

```bash
.venv/bin/python -m pytest tests/ -k operational -q   # all no_db; PostgreSQL never contacted
```

# Curunír

An intelligence workbench that will not let a conclusion outrun its evidence.

Curunír collects public evidence, preserves it byte-for-byte under custody,
turns it into a temporal world model of entities, claims and change, and lets
analysts reason over that model — hypotheses, forecasts, warnings — and publish
a dossier in which **every settled sentence is content-bound to a cited record**.
Two different people must sign a release. The whole mission replays offline from
its own export, with no network and no model provider.

It is not accredited, not a system of record, and not validated intelligence.
`argus_demo/CURUNIR_V6_8_QUALIFICATION.json` states exactly what has and has not
been established.

---

## Where things are

> **Naming.** The product lives under `argus_demo/`. The directory name is
> historical and misleading — it is the product root, not a demo. Renaming it
> would break the frozen V6.8 campaign identity, the kernel mount path, and the
> pilot protocol, so it stays until there is a reason to pay that cost.

| Path | What it is |
|---|---|
| `argus_demo/curunir_fabric/` | Collection: source registry, guarded egress, connectors (GLEIF, SEC EDGAR, Wikidata, Wayback, RSS, web pages), watches |
| `argus_demo/curunir_semantic/` | Normalization and extraction → documents, observations, claims, world model, change detection |
| `argus_demo/curunir_analytic/` | Themes, narratives, stakeholders, impact, forecasts, indicators, warnings, analogues |
| `argus_demo/curunir_operational/` | **Trusted core**: hash-chained store, access lattice, record ontology, canonical byte contract |
| `argus_demo/curunir_identity/` | Ed25519 actor identity, sessions, signed actions, replay verification |
| `argus_demo/curunir_workbench/` | HTTP command surface + the analyst SPA |
| `argus_demo/tests/` | 138 modules, ~36k lines |
| `argus_demo/tools/` | Campaign harnesses and validators (`curunir_v68.py`, `validate_v67.py`) |

Roughly 33k lines of product against 36k lines of tests.

### Not the product

`argus_demo/argus_capsules/`, `argus_demo/argus_neural/` and
`argus_demo/artifacts/` are separate ARGUS research lines. They share the repo
and nothing else. Their tests are the residual failures described below.

---

## External prerequisites

Curunír is reproducible from tracked state **plus** two things that deliberately
live outside it. `argus_demo/CURUNIR_RECONSTRUCTION.md` is the authority.

1. **The ARGUS kernel** — a versioned snapshot mounted at `argus_demo/argus/`,
   pinned by a whole-tree hash. Eight modules are imported directly. Do not
   vendor it; mount the identified snapshot.
2. **Pinned Python deps** — Python ≥ 3.12 with `cryptography==44.0.0`, `fastapi`,
   `uvicorn`, `pydantic`, `httpx`. `playwright` is test-only. `pdftotext`
   (poppler-utils) is optional; a missing binary is recorded as a bounded
   processing failure, never as absence of evidence.

## Running it

```bash
cd argus_demo
export PYTHONPATH="$PWD"

.venv/bin/python -m pytest tests/ -q              # the suite
.venv/bin/python -m curunir_operational.cli --help # store, export, replay, verify

CURUNIR_MISSION_ROOT=/path/to/mission CURUNIR_ACTORS=/path/to/actors.json \
  .venv/bin/uvicorn curunir_workbench.server:app   # the workbench
```

Restore and replay of a mission need **no network and no providers** — replay is
pure log replay over the exported package.

### Expected test result

The `curunir_*` product plane passes. The suite also collects the ARGUS research
lines, which need corpora that are not tracked here; those account for every
residual failure. `argus_demo/CURUNIR_V6_9_EXCISION.json` records the exact
non-passing node set, and the V6.8 harness tests refuse a dirty checkout by
design — commit before running them.

---

## Reading the documents

`argus_demo/CURUNIR_DOCUMENTS.md` is the index: what each contract, ledger and
protocol is, whether it is frozen or live, and which to read first. Start there
rather than opening the `CURUNIR_*` files in alphabetical order.

## Design commitments

These are enforced in code, not aspirational:

- **No write-down.** A derived record is floored on its inputs' markings at one
  chokepoint. A refresh never re-classifies downward.
- **Content-binding.** A `SUPPORTED` sentence must *be* the cited statement or
  value. A citation-shaped reference is not enough.
- **Fail closed.** Unknown, malformed, stale, replayed or tampered input is a
  typed refusal with no partial authoritative state.
- **Human authority.** A model may propose; only a human accepts. Approval is an
  Ed25519 signed act bound to actor, mission, report and version, and the author
  may not be the approver.
- **Exportable.** Documented open formats, full lineage, no vendor-specific
  transformation required to leave.

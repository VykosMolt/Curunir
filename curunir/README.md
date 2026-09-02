# Curunír

An intelligence workbench that will not let a conclusion outrun its evidence.

Curunír collects public evidence, preserves it byte-for-byte under custody,
turns it into a temporal world model of entities, claims and change, and lets
analysts reason over that model — hypotheses, forecasts, warnings — and publish
a dossier in which **every settled sentence is content-bound to a cited record**.
Two different people must sign a release. The whole mission replays offline from
its own export, with no network and no model provider.

It is not accredited, not a system of record, and not validated intelligence.
`CURUNIR_V6_8_QUALIFICATION.json` states exactly what has and has not been
established.

---

## Where things are

This directory is the product root. It was called `argus_demo/` until
2026-09-02, when the manifests were revised to the new layout (see
`CURUNIR_DOCUMENTS.md`). `OVERVIEW.md` is the node map.

| Path | What it is |
|---|---|
| `curunir_fabric/` | Collection: source registry, guarded egress, connectors (GLEIF, SEC EDGAR, Wikidata, Wayback, RSS, web pages), watches |
| `curunir_semantic/` | Normalization and extraction → documents, observations, claims, world model, change detection |
| `curunir_analytic/` | Themes, narratives, stakeholders, impact, forecasts, indicators, warnings, analogues |
| `curunir_operational/` | **Trusted core**: hash-chained store, access lattice, record ontology, canonical byte contract |
| `curunir_identity/` | Ed25519 actor identity, sessions, signed actions, replay verification |
| `curunir_workbench/` | HTTP command surface + the analyst SPA |
| `tests/` | the product suite: 64 modules plus the `v67/` security tranche |
| `tools/` | Campaign harnesses and validators (`curunir_v68.py`, `validate_v67.py`, `reconstruct_v67.py`) |
| `v68/` | frozen notional mission fixtures |

Roughly 33k lines of product against 36k lines of tests.

### Not here

The ARGUS kernel lives in `../kernel/argus/`, untracked and hash-pinned. The
neural and capsule research lines live in the Saulot repository, not here.

---

## External prerequisites

Curunír is reproducible from tracked state **plus** two things that deliberately
live outside it. `CURUNIR_RECONSTRUCTION.md` is the authority.

1. **The ARGUS kernel** — `../kernel/argus/`, pinned by a whole-tree hash
   (`../kernel/README.md`). Eight modules are imported directly. Do not vendor
   it into this directory; the harness and the reconstruction test locate it
   through `CURUNIR_ARGUS_KERNEL` or the default `../kernel/argus`.
2. **Pinned Python deps** — Python ≥ 3.12 with the hash-pinned
   `requirements.txt` (`cryptography==44.0.0`, `fastapi`, `uvicorn`, `pydantic`,
   `httpx`; `playwright` for browser tests; `psycopg` and a CPU `torch` for the
   sibling planes). `pdftotext` (poppler-utils) is optional.

An **analytical model provider** (`anthropic` or `openai`) is optional and
deliberately outside the pinned set — see `curunir_analytic/README.md`.

## Running it

```bash
cd curunir
export PYTHONPATH="$PWD:$PWD/../kernel"

.venv/bin/python -m pytest tests/ -q -p no:cacheprovider     # the suite
.venv/bin/python -m curunir_operational.cli --help            # store, export, replay, verify

CURUNIR_MISSION_ROOT=/path/to/mission CURUNIR_ACTORS=/path/to/actors.json \
  .venv/bin/uvicorn curunir_workbench.server:app               # the workbench
```

Restore and replay of a mission need **no network and no providers** — replay is
pure log replay over the exported package. Never point a dev server at a V6.8
mission root under `../curunir_v68_runs/`; copy it first.

### Expected test result

The `curunir_*` product plane passes. Three things are environmental, not
defects: `tests/v67/test_clean_reconstruction.py` needs the kernel;
`tests/test_curunir_v68.py` refuses a dirty checkout by design (commit first);
the browser journeys need Playwright's Chromium (`python -m playwright install
chromium`). `test_operational_v2_integration::test_live_vs_synthetic_distinct`
is the one known pre-existing failure — it needs untracked live capture fixtures.

## Reading the documents

`CURUNIR_DOCUMENTS.md` is the index: what each contract, ledger and protocol
is, whether it is frozen or live, and which to read first. If you are resuming
work, `CURUNIR_V6_9_HANDOFF.md` first.

## Design commitments

These are enforced in code, not aspirational:

- **No write-down.** A derived record is floored on its inputs' markings at one
  chokepoint. A refresh never re-classifies downward.
- **Content-binding.** A `SUPPORTED` sentence must *be* the cited statement or
  value. A citation-shaped reference is not enough.
- **Fail closed.** Unknown, malformed, stale, replayed or tampered input is a
  typed refusal with no partial authoritative state.
- **Human authority.** A model may propose; only a human accepts. Approval is an
  Ed25519 signed act bound to actor, mission, report and version, and the
  author may not be the approver.
- **Exportable.** Documented open formats, full lineage, no vendor-specific
  transformation required to leave.

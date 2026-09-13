# Curunír

An intelligence workbench that will not let a conclusion outrun its evidence.

Curunír collects public evidence, preserves it byte-for-byte under custody,
turns it into a temporal world model of entities, claims and change, and lets
analysts reason over that model — hypotheses, forecasts, warnings — and publish
a dossier in which **every settled sentence is content-bound to a cited record**.
Two different people must sign a release. A whole mission replays offline from
its own export, with no network and no model provider.

> **Status.** This is a research prototype. It is not accredited, not a system
> of record, and not validated intelligence.
> [`curunir/CURUNIR_V6_8_QUALIFICATION.json`](curunir/CURUNIR_V6_8_QUALIFICATION.json)
> states exactly what has and has not been established, including the limits of
> the pilot that produced it.

## Design commitments

These are enforced in code, not aspirational:

- **No write-down.** A derived record is floored on its inputs' markings at one
  chokepoint. A refresh never re-classifies downward.
- **Content-binding.** A `SUPPORTED` sentence must *be* the cited statement or
  value. A citation-shaped reference is not enough.
- **Fail closed.** Unknown, malformed, stale, replayed or tampered input is a
  typed refusal, with no partial authoritative state left behind.
- **Human authority.** A model may propose; only a human accepts. Approval is an
  Ed25519 signed act bound to actor, mission, report and version, and the
  author may not be the approver.
- **Exportable.** Documented open formats, full lineage, no vendor-specific
  transformation required to leave.

## Layout

| Path | What it is | Start at |
|---|---|---|
| **`curunir/`** | The product: six `curunir_*` packages, the workbench, the V6.7/V6.8/V6.9 contracts and ledgers, the pilot harness and the test suite. | [`curunir/README.md`](curunir/README.md) |
| **`kernel/`** | Documentation of the one external dependency, the hash-pinned ARGUS kernel. The tree itself is untracked and verified against a whole-tree hash. | [`kernel/README.md`](kernel/README.md) |

Inside `curunir/`:

| Package | Plane |
|---|---|
| `curunir_fabric/` | Collection: source registry, guarded egress, connectors (GLEIF, SEC EDGAR, Wikidata, Wayback, RSS, web pages), watches |
| `curunir_semantic/` | Normalization and extraction → documents, observations, claims, world model, change detection |
| `curunir_analytic/` | Themes, narratives, stakeholders, impact, forecasts, indicators, warnings, analogues |
| `curunir_operational/` | **Trusted core**: hash-chained store, access lattice, record ontology, canonical byte contract |
| `curunir_identity/` | Ed25519 actor identity, sessions, signed actions, replay verification |
| `curunir_workbench/` | HTTP command surface and the analyst SPA |

Roughly 33k lines of product against 36k lines of tests.

[`NAVIGATION.md`](NAVIGATION.md) is the map of every directory README;
[`HOUSEKEEPING.md`](HOUSEKEEPING.md) is the rule set for what lives where and
how anything superseded leaves.

## Getting started

**Prerequisites:** Python ≥ 3.12 and the pinned ARGUS kernel (below).
`pdftotext` (poppler-utils) is optional, for PDF ingestion.

### The kernel

A fresh clone has no kernel. Curunír imports eight modules from it and verifies
the whole tree against a frozen hash before any campaign or reconstruction step
runs, so fetch it first. It is published as the GitHub release
[`kernel-4c173df7`](https://github.com/VykosMolt/Curunir/releases/tag/kernel-4c173df7):

```bash
git clone https://github.com/VykosMolt/Curunir.git
cd Curunir

gh release download kernel-4c173df7 --pattern 'argus_kernel_pinned_*.tar.gz' --dir kernel
gh release download kernel-4c173df7 --pattern schema.sql --dir kernel
python curunir/tools/kernel_bundle.py unpack kernel/argus_kernel_pinned_4c173df7.tar.gz --into kernel
```

`unpack` refuses any tree whose hash is not the pinned one, and prints the hash
and file count it accepted. Do not vendor the kernel into `curunir/`. The
default location is `../kernel/argus`; override it with `CURUNIR_ARGUS_KERNEL`.
See [`kernel/README.md`](kernel/README.md) for how to re-verify it later.

### The product

```bash
cd curunir
python -m venv .venv
.venv/bin/pip install -r requirements.txt          # hash-locked
.venv/bin/python -m playwright install chromium    # for the browser journeys
```

### Running

```bash
cd curunir
export PYTHONPATH="$PWD:$PWD/../kernel"

.venv/bin/python -m pytest tests/ -q -p no:cacheprovider     # the suite
.venv/bin/python -m curunir_operational.cli --help           # store, export, replay, verify

CURUNIR_MISSION_ROOT=/path/to/mission CURUNIR_ACTORS=/path/to/actors.json \
  .venv/bin/uvicorn curunir_workbench.server:app             # the workbench
```

Restore and replay of a mission need **no network and no model provider** —
replay is pure log replay over the exported package.

An analytical model provider (`anthropic` or `openai`) is optional and
deliberately outside the pinned dependency set; the `deterministic` backend is
offline and is what the tests use. See
[`curunir/curunir_analytic/README.md`](curunir/curunir_analytic/README.md).

### Expected test result

The `curunir_*` product plane passes. Three things are environmental rather than
defects: `tests/v67/test_clean_reconstruction.py` needs the kernel;
`tests/test_curunir_v68.py` refuses a dirty checkout by design (commit first);
the browser journeys need Playwright's Chromium.
`test_operational_v2_integration::test_live_vs_synthetic_distinct` is the one
known pre-existing failure — it needs live capture fixtures that are not tracked.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the suite on every
push and pull request: it installs the hash-locked requirements and Chromium,
downloads the kernel tarball from the `kernel-4c173df7` release, verifies its
tree hash with `curunir/tools/kernel_bundle.py`, and runs the tests.

## The documents

Curunír is governed by frozen contracts rather than by prose. Several are
hash-pinned by another document, and changing one is a change-control event
(`HOUSEKEEPING.md` §4). [`curunir/CURUNIR_DOCUMENTS.md`](curunir/CURUNIR_DOCUMENTS.md)
is the index: what each contract, ledger and protocol is, whether it is frozen
or live, and which to read first.

The product was carved out of an earlier research repository on 2026-09-02 with
its full history; `curunir/CURUNIR_COMMIT_MAP_SAULOT.txt` maps every commit id
from that repository to its id here, so the hashes cited in frozen evidence can
still be followed. The ARGUS research lines (neural extractor, codec capsules)
stayed behind and are not part of this repository.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). The ARGUS
kernel is a separate external dependency and is not covered by it.

# curunir — the product root

Every runnable path starts here. Until 2026-09-02 this directory was called
`argus_demo/` (ARGUS was Curunír's earlier name); the manifests were revised to
the new name that day, and only the untracked pilot evidence still says
`argus_demo`. The ARGUS kernel is no longer mounted inside it: it lives in
`../kernel/argus`.

```bash
cd curunir && export PYTHONPATH="$PWD:$PWD/../kernel"
```

## What lives here

| Node | What it is | Its own doc |
|---|---|---|
| `curunir_fabric/` | collection — sources, guarded egress, connectors, watches | `curunir_fabric/README.md` |
| `curunir_semantic/` | normalization and extraction → world model, claims, change | `curunir_semantic/README.md` |
| `curunir_analytic/` | themes, narratives, stakeholders, impact, forecasts, warnings, model providers | `curunir_analytic/README.md` |
| `curunir_operational/` | the trusted core — store, access lattice, ontology, canonical bytes | `curunir_operational/README.md` |
| `curunir_identity/` | Ed25519 actor identity, sessions, signed actions | `curunir_identity/README.md` |
| `curunir_workbench/` | HTTP command surface and the analyst SPA | `curunir_workbench/README.md` |
| `tests/` | the product suite (64 modules + `v67/`) | `tests/README.md` |
| `tools/` | campaign harnesses and validators | `tools/README.md` |
| `v68/` | frozen notional mission fixtures | `v68/README.md` |
| `CURUNIR_*.{md,json}` | contracts, ledgers, protocols | **`CURUNIR_DOCUMENTS.md`** |

## Not here

| Node | What it is |
|---|---|
| `../kernel/argus/` | the **external ARGUS kernel** — a hash-pinned snapshot, not owned by this directory. Curunír imports 8 modules from it. Do not vendor it, do not edit it. `../kernel/README.md` has the pin. |
| Saulot repository | the earlier ARGUS research lines (neural extractor, codec capsules) with their artifacts and tests, and the kernel's Postgres kit. Not in this repository. |

## The data flow, once

```
source registry ─▶ guarded egress ─▶ connector ─▶ CUSTODY (bytes hashed, preserved)
                                                        │
                            normalize ─▶ documents ─▶ observations ─▶ CLAIMS
                                                        │
              themes · narratives · stakeholders · impact · forecasts · warnings
                                                        │
                        hypotheses ─▶ collection ─▶ new evidence ─▶ reassessment
                                                        │
                         dossier: every SUPPORTED sentence content-bound to a cite
                                                        │
                        two distinct humans ─▶ Ed25519 signed disposition
                                                        │
                              export ─▶ offline replay, no network, no provider
```

Each arrow is a place something can be refused. Nothing downstream can be less
restricted than what it rests on, and nothing settled can outrun its evidence.

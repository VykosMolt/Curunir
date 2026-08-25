# argus_demo — the product root

Despite the name, this is not a demo. It is the Curunír product root, and every
runnable path starts here. The name is historical: ARGUS was Curunír's earlier
name. Renaming the directory would break the frozen V6.8 campaign identity, the
kernel mount path, and the pilot protocol, so it stays until there is a reason
to pay that cost.

```bash
cd argus_demo && export PYTHONPATH="$PWD"
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
| `tests/` | 138 modules | `tests/README.md` |
| `tools/` | campaign harnesses and validators | `tools/README.md` |
| `v68/` | frozen notional mission fixtures | `v68/README.md` |
| `CURUNIR_*.{md,json}` | contracts, ledgers, protocols | **`CURUNIR_DOCUMENTS.md`** |

## Not the product

| Node | What it is |
|---|---|
| `argus/` | the **external ARGUS kernel** — a hash-pinned snapshot mounted here, not owned by this repository. Curunír imports 8 modules from it. Do not vendor it, do not edit it. |
| `argus_neural/`, `argus_capsules/` | earlier ARGUS research lines (neural extractor, codec capsules) that share the repo. Their tests need corpora that are not tracked. |
| `artifacts/` | campaign outputs — see `artifacts/README.md` for what survives and what was pruned |

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

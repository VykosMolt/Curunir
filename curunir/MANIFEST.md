# curunir/ — manifest

The product plane. Rules: `../HOUSEKEEPING.md`. This file says what belongs
here and what does not.

## Belongs here

| Path | Tracked | Role |
|---|---|---|
| `curunir_fabric/ curunir_semantic/ curunir_analytic/ curunir_operational/ curunir_identity/ curunir_workbench/` | yes | the six product packages; each has a README |
| `tests/` | yes | the product suite; `v67/` is the security tranche; `*_support.py` shared fixtures; `js/` the canonical-parity harness |
| `tools/` | yes | `curunir_v68.py`, `validate_v67.py`, `reconstruct_v67.py`, `reconstruction_smoke.py` |
| `v68/` | yes | frozen notional mission fixtures (hash-verified by the harness) |
| `CURUNIR_*.md`, `CURUNIR_*.json`, `CURUNIR_V6_7_REFERENCE_COMMITS.txt`, `CURUNIR_COMMIT_MAP_SAULOT.txt` | yes | contracts, ledgers, evidence, handoffs, the Saulot commit map — indexed in `CURUNIR_DOCUMENTS.md` |
| `README.md`, `OVERVIEW.md`, `MANIFEST.md`, `C11_CAPABILITY_GAPS_HANDOFF.md` | yes | orientation |
| `requirements.txt` | yes, **hash-locked** | never edit |
| `.gitignore` | yes | plane-local ignores |
| `conftest.py`, `pytest.ini` | no (excluded) | puts this root and `../kernel` on `sys.path` |
| `.venv/` | no | the one virtualenv all three planes use |
| `missions/` | no | the workbench demo mission (`apple_workbench_v66`) |
| `docs/` | no | Curunír design docs, preregistrations and review guides, July 2026 — historical |
| `research/` | no | the 51 `curunir-*` research reports and their ledger — historical |

## Does not belong here

- The ARGUS kernel (`../kernel/argus`). Never mount or copy it in here; the
  reconstruction tool refuses tracked state that contains it.
- `argus_neural`, `argus_capsules`, `artifacts/` — the Saulot repository.
- The kernel's Postgres kit, corpus and inboxes — the Saulot repository.
  `schema.sql` alone sits in `../kernel`, untracked.
- Campaign output roots (`*_2026MMDD/`), `p2.json`-style run dumps, contract
  scratch directories. Closed campaigns are deleted with a ledger line.
- Tests that import from `curunir_operational.v3` … `v5_8_1`: that tree was
  excised in V6.9 (`CURUNIR_V6_9_EXCISION.json`, tag
  `archive/curunir-campaign-tree-v5x`). Such a test is dead and is deleted.

## Superseded and gone (2026-09-02)

- `argus_demo/` naming; the in-tree kernel mount; `284_d42e_contract/`,
  `p2.json`; byte-code-only `curunir_operational/v3 … v5_8_1` shells;
  `tests/test_gate11_integrity_repairs.py` (imported the excised `v4.kernel`).

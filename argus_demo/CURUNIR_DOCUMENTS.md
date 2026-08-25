# Curunír documents — what each one is, and what to read first

Seventeen `CURUNIR_*` files sit beside the source (this one included). They are not interchangeable:
some are **frozen authority** (a contract sealed before the work it governs,
sometimes hash-pinned by another document), some are **evidence** (what a
campaign measured), and some are **live reference** (kept current). Editing a
frozen document is a change-control event, not a docs fix.

## Read these first

| Order | Document | Why |
|---|---|---|
| 0 | `CURUNIR_V6_9_HANDOFF.md` | If you are resuming work, read this before anything else |
| 1 | `../README.md` | What Curunír is, where everything lives, how to run it |
| 2 | `CURUNIR_V6_8_QUALIFICATION.json` | The honest statement of what has and has not been established |
| 3 | `CURUNIR_V6_7_ARCHITECTURE.md` | The ten-point trusted-core contract the code is built to |
| 4 | `CURUNIR_RECONSTRUCTION.md` | What is tracked, what is external, how to rebuild |

If you are about to change code, add `CURUNIR_V6_7_INVARIANTS.json` — it is the
machine-readable list of properties that must not break.

## Full index

### Live reference — kept current

| Document | Contents |
|---|---|
| `CURUNIR_RECONSTRUCTION.md` | The tracked/external boundary: the 8 kernel modules imported directly, the kernel whole-tree hash, the pinned deps, and the reconstruction steps. Proven by `tests/v67/test_clean_reconstruction.py`. |
| `CURUNIR_DOCUMENTS.md` | This index. |

### V6.7 — the clean rewrite (security and information-flow integrity)

| Document | Status | Contents |
|---|---|---|
| `CURUNIR_V6_7_ARCHITECTURE.md` | **Frozen before implementation** | The small trusted core: ten numbered ownership rules, failure semantics, forbidden shortcuts, escalation conditions. |
| `CURUNIR_V6_7_INVARIANTS.json` | **Frozen** | Machine-readable invariant ledger keyed to the rewrite base and the archived reference head. |
| `CURUNIR_V6_7_INDEPENDENT_REVIEW.md` | Evidence | Three independent adversarial reviews of the rewrite. Two returned `REJECT`; the third `ACCEPT`. Every finding, its repair, and its causal lock. |
| `CURUNIR_V6_7_BASELINE_NONPASSING.json` | **Frozen** | The exact accepted non-passing node set, hash-pinned, so a failure/error kind change is refused rather than absorbed. |
| `CURUNIR_V6_7_RECONSTRUCTION.json` | Reference | Machine-readable form of the reconstruction contract. |
| `CURUNIR_V6_7_REPOSITORY_TRUTH.json` | Evidence | Repository state captured at freeze time. |
| `CURUNIR_V6_7_REFERENCE_COMMITS.txt` | Reference | The clean base, the archived reference ref, and the commit lineage behind them. |

### V6.8 — the human pilot (does it work when real people use it?)

| Document | Status | Contents |
|---|---|---|
| `CURUNIR_V6_8_QUALIFICATION.json` | **Frozen before the pilot** | Objective, invariants, source policy, measurement rules, evidence and replay requirements, failure classes, and the terminal verdict definitions. Hash-pins the protocol and the repair contract. |
| `CURUNIR_V6_8_MISSIONS.json` | **Frozen** | The three missions: M1 corporate-registry capstone (live sources), M2 regulatory correction, M3 relief collaboration. Question, permitted sources, required mechanisms and completion criteria for each. |
| `CURUNIR_V6_8_PILOT_PROTOCOL.md` | **Frozen** (hash-pinned by the qualification) | How the pilot is actually run: custody, two distinct human participants, session discipline, per-mission steps. |
| `CURUNIR_V6_8_REPAIR_CONTRACT.json` | **Frozen before repair** | What was permitted to be fixed between the reviewed executable and the pilot. |
| `CURUNIR_V6_8_REPOSITORY_TRUTH.json` | Evidence | Repository state bound to the pilot. |
| `CURUNIR_V6_8_V7_FOLLOW_UP.json` | Live ledger | Capability needs observed during the pilot that belong to V7, recorded rather than implemented. Currently empty. |

Pilot results are **not** here — they are in `../curunir_v68_runs/`, one directory
per campaign root. `V68_TERMINAL_004` is the canonical human pilot: M1 completed
and frozen, M2 signed but harness-`NOT_ACHIEVED`, M3 prepared and not run.

### V6.9 — the excision

| Document | Status | Contents |
|---|---|---|
| `CURUNIR_V6_9_HANDOFF.md` | Live | **Start here to resume work.** Branch and head, current state, what the last session did, the traps that cost it time, what is open and in what order. |
| `CURUNIR_V6_9_EXCISION.json` | Ledger | Removal of the `v3`..`v5_8_1` research-campaign tree: what was lifted first and how equivalence was proven, what was removed, what was retained and why, and the measured node-set effect (zero new nodes, zero outcome-kind changes). The removed files live at tag `archive/curunir-campaign-tree-v5x`. |

## Conventions

- A document saying `FROZEN_BEFORE_...` was sealed before the work it governs, so
  that the work could not quietly redefine its own success condition. Several are
  hash-pinned by another document; changing the bytes breaks that pin on purpose.
- Node-set ledgers exist so a test can never be deleted, skipped or downgraded to
  make a run look better. Any change to collection is declared in a ledger.
- "Evidence" documents record what was measured, including what failed. They are
  not edited to improve the result.

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
| 1 | `README.md` (this directory) and `../README.md` | What Curunír is, where everything lives, how to run it |
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

## Revision of 2026-09-02 — the repository split

Curunír was carved out of the Saulot repository with `git filter-repo` on
2026-09-02, and the frozen manifests were revised the same day. What changed
and how it is accounted for:

| Change | Where it is recorded |
|---|---|
| Paths: `argus_demo` → `curunir`, `argus_demo/argus` → `kernel/argus`, `/home/moloch/Saulot` → `/home/moloch/Curunir`, worktrees folded into `main` | every revised JSON carries a `revised_2026_09_02` block with the sha256 it supersedes; the protocol has a revision note at its end |
| Commit ids: every id in the frozen documents is now a Curunír id | `CURUNIR_COMMIT_MAP_SAULOT.txt` — one `old new` pair per line, covering `main` and the V6.7 reference lineage (tag `archive/curunir-v67-round38-33330efe6ae3`, whose *name* keeps the Saulot short id as a label) |
| The five V6.8 authority files: `QUALIFICATION`, `MISSIONS` (byte-identical), `REPOSITORY_TRUTH`, `REPAIR_CONTRACT`, `PILOT_PROTOCOL` | `CURUNIR_V6_8_QUALIFICATION.json` re-pins the protocol and repair contract and ledgers the superseded hash set under `authority_supersession`; `tools/curunir_v68.py::_accepted_authorities` accepts a campaign root's authority set only if it is the current one or a ledgered one, and `tests/test_curunir_v68.py` proves an unledgered set is refused |
| Pilot evidence (`../curunir_v68_runs/`) | **not edited.** It records Saulot ids and the superseded hashes; `V68_TERMINAL_004`'s three missions verify through the ledger |
| The pinned kernel (`../kernel/argus/`) | untouched; three of its scripts still contain `argus_demo` strings, which is exactly what the hash guarantees |

The qualification contract's `supersession_chain` lists the byte identities it
has passed through, oldest first.

## Conventions

- A document saying `FROZEN_BEFORE_...` was sealed before the work it governs, so
  that the work could not quietly redefine its own success condition. Several are
  hash-pinned by another document; changing the bytes breaks that pin on purpose.
- Node-set ledgers exist so a test can never be deleted, skipped or downgraded to
  make a run look better. Any change to collection is declared in a ledger.
- "Evidence" documents record what was measured, including what failed. They are
  not edited to improve the result.

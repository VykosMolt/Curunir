# Navigation map

Every directory that contains anything non-obvious carries its own `README.md`.
This file is the map of those maps: start here, then descend.

```
Curunir/
├── README.md ........................ what this repository is and how to run the product
├── NAVIGATION.md ................... you are here
├── HOUSEKEEPING.md ................. what lives where, what may be deleted, how
│
├── curunir/ ........................ THE PRODUCT (was Saulot/argus_demo/ until 2026-09-02)
│   ├── README.md ................... what Curunír is, design commitments, expected test result
│   ├── MANIFEST.md ................. what belongs in this directory and what does not
│   ├── CURUNIR_V6_9_HANDOFF.md ..... START HERE if you are resuming work
│   ├── OVERVIEW.md ................. the node map for everything below
│   ├── CURUNIR_DOCUMENTS.md ........ index of all contracts and ledgers, and the frozen-path note
│   ├── CURUNIR_COMMIT_MAP_SAULOT.txt  Saulot commit id → commit id here
│   ├── curunir_fabric/ ............. COLLECTION (README, connectors/README)
│   ├── curunir_semantic/ ........... NORMALIZE → WORLD MODEL
│   ├── curunir_analytic/ ........... ANALYSIS, model providers
│   ├── curunir_operational/ ........ THE TRUSTED CORE (README, scenario/README)
│   ├── curunir_identity/ ........... WHO ACTED
│   ├── curunir_workbench/ .......... THE OPERATOR SURFACE (README, static/README)
│   ├── tests/ ...................... the product suite (README, v67/README)
│   ├── tools/ ...................... pilot harness, reconstruction, validators
│   ├── v68/ ........................ frozen notional mission fixtures
│   ├── docs/, research/ ............ design docs and campaign reports (untracked, historical)
│   ├── missions/ ................... the workbench demo mission (untracked)
│   └── .venv/ ...................... the virtualenv (untracked)
│
├── kernel/ ......................... KERNEL GOLD — the external dependency
│   ├── README.md ................... the pin, how to verify and restore
│   ├── MANIFEST.md
│   ├── argus/ ...................... the hash-pinned ARGUS kernel — untracked, DO NOT EDIT
│   ├── argus_kernel_pinned_4c173df7.tar.gz  the backup of that tree (untracked)
│   └── schema.sql .................. hash-checked by tests/test_operational_protection.py (untracked)
│
└── curunir_v68_runs/ ............... human pilot evidence (untracked, single copy, see its README)
```

## Reading orders

**"What is this and does it work?"**
`README.md` → `curunir/CURUNIR_V6_8_QUALIFICATION.json` → `curunir_v68_runs/`

**"I am going to change the code."**
`curunir/OVERVIEW.md` → `curunir/CURUNIR_V6_7_ARCHITECTURE.md` →
`curunir/CURUNIR_V6_7_INVARIANTS.json` → the README of the plane you are touching →
`curunir/tests/README.md`

**"I need to rebuild it somewhere else."**
`curunir/CURUNIR_RECONSTRUCTION.md` → `kernel/README.md` → `curunir/tools/README.md`

**"What happened historically?"**
`curunir/CURUNIR_DOCUMENTS.md` → `curunir/CURUNIR_COMMIT_MAP_SAULOT.txt` →
git tag `archive/curunir-campaign-tree-v5x` → the Saulot repository

## Conventions

- A directory's `README.md` is authoritative for that directory. If code and
  README disagree, that is a bug in the README — fix it in the same commit.
- A document marked **frozen** was sealed before the work it governs. Several are
  hash-pinned by another document. Editing one is a change-control event, which
  is why the 2026-09-02 split left them saying `argus_demo`.
- Anything removed is recoverable from a git tag named in the ledger that
  removed it. Nothing is deleted without a ledger entry.

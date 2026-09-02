# Navigation map

Every directory that contains anything non-obvious carries its own `README.md`.
This file is the map of those maps: start here, then descend.

```
Saulot/
├── README.md ........................ the three planes and how to run Curunír
├── NAVIGATION.md ................... you are here
│
├── curunir/ ........................ THE PRODUCT (was argus_demo/ until 2026-09-02)
│   ├── README.md ................... what Curunír is, design commitments, expected test result
│   ├── CURUNIR_V6_9_HANDOFF.md ..... START HERE if you are resuming work
│   ├── OVERVIEW.md ................. the node map for everything below
│   ├── CURUNIR_DOCUMENTS.md ........ index of all contracts and ledgers, and the frozen-path note
│   ├── curunir_fabric/ ............. COLLECTION (README, connectors/README)
│   ├── curunir_semantic/ ........... NORMALIZE → WORLD MODEL
│   ├── curunir_analytic/ ........... ANALYSIS, model providers
│   ├── curunir_operational/ ........ THE TRUSTED CORE (README, scenario/README)
│   ├── curunir_identity/ ........... WHO ACTED
│   ├── curunir_workbench/ .......... THE OPERATOR SURFACE (README, static/README)
│   ├── tests/ ...................... the product suite (README, v67/README)
│   ├── tools/ ...................... pilot harness, reconstruction, validators
│   ├── v68/ ........................ frozen notional mission fixtures
│   ├── docs/, research/ ............ Curunír design docs and campaign reports (untracked, historical)
│   ├── missions/ ................... the workbench demo mission
│   └── .venv/ ...................... the one virtualenv, shared by all three planes
│
├── kernel/ ......................... KERNEL GOLD
│   ├── README.md ................... the pin, how to verify and restore, the ARGUS demo kit
│   ├── argus/ ...................... the hash-pinned ARGUS kernel — DO NOT EDIT
│   ├── argus_kernel_pinned_4c173df7.tar.gz  the only backup of that tree
│   ├── schema.sql, docker-compose.yml, .env.example  the Postgres spine the kernel writes to
│   ├── review_app/, demo_corpus/, exports/  the ARGUS demo kit
│   ├── content_store/, offline_source_inbox/, review_inbox/  ingestion and review intake
│   ├── docs/ ....................... ARGUS-era design and findings (untracked)
│   └── tests/ ...................... the ARGUS research-line suite (README)
│
├── capsules/ ....................... NEURAL EXTRACTOR + CODEC CAPSULES
│   ├── README.md
│   ├── argus_neural/ ............... the universal neural extractor line
│   ├── argus_capsules/ ............. the codec / story-capsule line
│   ├── artifacts/ .................. trained checkpoints and campaign outputs (README)
│   ├── argus_tiny256_story_capsule_v0_20260628_1525.tar.gz, TINY256_STORY_CAPSULE_V0_FROZEN.txt
│   └── tests/ ...................... the capsule and neural suite (README)
│
├── curunir_v68_runs/ ............... human pilot evidence (untracked, single copy, see its README)
├── 02_CONTRACT/ .................... schema contracts (netwatch)
└── .worktrees/ ..................... netwatch federation worktrees — untouched
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
`curunir/CURUNIR_DOCUMENTS.md` → `capsules/artifacts/README.md` → git tags `archive/*`

## Conventions

- A directory's `README.md` is authoritative for that directory. If code and
  README disagree, that is a bug in the README — fix it in the same commit.
- A document marked **frozen** was sealed before the work it governs. Several are
  hash-pinned by another document. Editing one is a change-control event, which
  is why the 2026-09-02 split left them saying `argus_demo`.
- Anything removed is recoverable from a git tag named in the ledger that
  removed it. Nothing is deleted without a ledger entry.

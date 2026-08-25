# Navigation map

Every directory that contains anything non-obvious carries its own `README.md`
explaining what is in it and why. This file is the map of those maps: start
here, then descend.

```
Saulot/
├── README.md ........................ what Curunír is, how to run it, design commitments
├── NAVIGATION.md ................... you are here
└── argus_demo/ ..................... THE PRODUCT ROOT (the name is historical)
    ├── CURUNIR_V6_9_HANDOFF.md ..... START HERE if you are resuming work
    ├── OVERVIEW.md ................. the node map for everything below
    ├── CURUNIR_DOCUMENTS.md ........ index of all 16 contracts and ledgers
    │
    ├── curunir_fabric/ ............. COLLECTION
    │   ├── README.md
    │   └── connectors/README.md .... GLEIF, EDGAR, Wikidata, Wayback, RSS, web
    │
    ├── curunir_semantic/ ........... NORMALIZE → WORLD MODEL
    │   └── README.md ............... documents, observations, claims, change
    │
    ├── curunir_analytic/ ........... ANALYSIS
    │   └── README.md ............... themes, forecasts, warnings, model providers
    │
    ├── curunir_operational/ ........ THE TRUSTED CORE
    │   ├── README.md ............... store, access lattice, ontology, canonical bytes
    │   └── scenario/README.md ...... synthetic Vessia Corridor fixtures
    │
    ├── curunir_identity/ ........... WHO ACTED
    │   └── README.md ............... Ed25519, sessions, signed actions, replay
    │
    ├── curunir_workbench/ .......... THE OPERATOR SURFACE
    │   ├── README.md ............... HTTP commands and projections
    │   └── static/README.md ........ the analyst SPA (and its known defects)
    │
    ├── tests/ ...................... 138 modules
    │   ├── README.md ............... layout, what to expect, house rules
    │   └── v67/README.md ........... the security tranche + historical exploit corpus
    │
    ├── tools/README.md ............. pilot harness, reconstruction, validators
    ├── v68/README.md ............... frozen notional mission fixtures
    ├── artifacts/README.md ......... campaign outputs: what survives, what was pruned
    │
    ├── argus/ ...................... EXTERNAL hash-pinned kernel — do not edit
    ├── argus_neural/ ............... earlier ARGUS research line
    └── argus_capsules/ ............. earlier ARGUS research line

curunir_v68_runs/ ................... human pilot evidence (untracked, see its README)
```

## Reading orders

**"What is this and does it work?"**
`README.md` → `argus_demo/CURUNIR_V6_8_QUALIFICATION.json` → `curunir_v68_runs/`

**"I am going to change the code."**
`argus_demo/OVERVIEW.md` → `CURUNIR_V6_7_ARCHITECTURE.md` →
`CURUNIR_V6_7_INVARIANTS.json` → the README of the plane you are touching →
`argus_demo/tests/README.md`

**"I need to rebuild it somewhere else."**
`argus_demo/CURUNIR_RECONSTRUCTION.md` → `argus_demo/tools/README.md`

**"What happened historically?"**
`argus_demo/CURUNIR_DOCUMENTS.md` → `argus_demo/artifacts/README.md` →
git tags `archive/*`

## Conventions

- A directory's `README.md` is authoritative for that directory. If code and
  README disagree, that is a bug in the README — fix it in the same commit.
- A document marked **frozen** was sealed before the work it governs, so the
  work could not redefine its own success condition. Several are hash-pinned by
  another document. Editing one is a change-control event.
- Anything removed is recoverable from a git tag named in the ledger that
  removed it. Nothing is deleted without a ledger entry.

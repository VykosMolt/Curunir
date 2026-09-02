# Saulot

Home of **Curunír** — an intelligence workbench that will not let a conclusion
outrun its evidence — and of the two ARGUS research lines it grew out of.

Since 2026-09-02 the repository is split into three planes. Each has its own
README, its own tests and its own `conftest.py`; they share one virtualenv.

| Plane | What it is | Start at |
|---|---|---|
| **`curunir/`** | The product: six `curunir_*` packages, the workbench, the V6.7/V6.8/V6.9 contracts and ledgers, the pilot harness. This is where work happens. | `curunir/README.md` → `curunir/CURUNIR_V6_9_HANDOFF.md` |
| **`kernel/`** | **Kernel Gold.** The inherited ARGUS claim kernel (`kernel/argus/`), hash-pinned and never edited, plus its tarball backup, its Postgres schema and demo kit, and the ARGUS-era research tests. Curunír imports eight modules from it. | `kernel/README.md` |
| **`capsules/`** | The ARGUS neural extractor and codec-capsule research lines: `argus_neural/`, `argus_capsules/`, their trained artifacts (`artifacts/private_capsules/`, the `universal_neural_extractor_*` campaigns) and tests. | `capsules/README.md` |

Also here:

- `curunir_v68_runs/` — **untracked, single-copy human pilot evidence.** Read its README before touching anything in it.
- `.worktrees/` — git worktrees for the netwatch federation line. Left exactly as they are.
- `02_CONTRACT/` — schema contracts (netwatch).

## Running Curunír

```bash
cd curunir
export PYTHONPATH="$PWD:$PWD/../kernel"

.venv/bin/python -m pytest tests/ -q -p no:cacheprovider          # the product suite
.venv/bin/python -m curunir_operational.cli --help                 # store, export, replay, verify

CURUNIR_MISSION_ROOT=/path/to/mission CURUNIR_ACTORS=/path/to/actors.json \
  .venv/bin/uvicorn curunir_workbench.server:app                    # the workbench
```

The kernel is resolved from `../kernel/argus` (override with
`CURUNIR_ARGUS_KERNEL`) and verified against the frozen whole-tree hash before
any campaign or reconstruction step runs.

## History of the layout

Until 2026-09-02 everything lived under one directory, `argus_demo/`, with the
kernel mounted inside it. Frozen manifests and evidence written before that
date still use those paths; `argus_demo` means `curunir` and `argus_demo/argus`
means `kernel/argus`. Nothing frozen was rewritten. `NAVIGATION.md` is the map.

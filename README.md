# Curunír

An intelligence workbench that will not let a conclusion outrun its evidence.

This repository holds the product (`curunir/`) and the documentation of its one
external dependency, the hash-pinned ARGUS kernel (`kernel/`). It was carved
out of the `Saulot` research repository on 2026-09-02 with the product's full
history; `curunir/CURUNIR_COMMIT_MAP_SAULOT.txt` maps every Saulot commit id to
its id here, so the commit hashes cited in frozen evidence can be followed.

| Path | What it is | Start at |
|---|---|---|
| **`curunir/`** | The product: six `curunir_*` packages, the workbench, the V6.7/V6.8/V6.9 contracts and ledgers, the pilot harness, the suite, the one virtualenv. | `curunir/README.md` → `curunir/CURUNIR_V6_9_HANDOFF.md` |
| **`kernel/`** | **Kernel Gold.** `kernel/argus/` is the inherited ARGUS kernel, untracked here by design and verified against one whole-tree hash; beside it the tarball backup and `schema.sql`. Only `README.md` and `MANIFEST.md` are tracked. | `kernel/README.md` |
| `curunir_v68_runs/` | **Untracked, single-copy human pilot evidence.** Read its README before touching anything in it. | |

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
any campaign or reconstruction step runs. A fresh clone has no kernel: unpack
`kernel/argus_kernel_pinned_4c173df7.tar.gz` there, or obtain the snapshot from
Saulot, then verify (`kernel/README.md`).

## History of the layout

Until 2026-09-02 the product lived in the Saulot repository as `argus_demo/`,
with the kernel mounted inside it. Frozen manifests and evidence written before
that date still use those paths; `argus_demo` means `curunir` and
`argus_demo/argus` means `kernel/argus`. Nothing frozen was rewritten.
`NAVIGATION.md` is the map; `HOUSEKEEPING.md` is the rule set that keeps it so.

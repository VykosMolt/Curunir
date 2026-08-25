# Tools — campaign harnesses and validators

Operational scripts, not product. Nothing in `curunir_*` imports these.

| Tool | Purpose |
|---|---|
| `curunir_v68.py` | the V6.8 pilot harness: prepares campaign roots, serves an instrumented workbench, records the operator event log, and derives the measurement, faithfulness and replay artifacts |
| `reconstruct_v67.py` | rebuild the product from committed bytes plus a mounted kernel; computes the kernel whole-tree hash |
| `validate_v67.py` | the terminal validator: collection counts, accepted-residual comparison, outcome-kind changes |
| `reconstruction_smoke.py` | fast reconstruction check |

## Two behaviours that look like failures and are not

`curunir_v68.py prepare-all` **refuses a dirty checkout**. A campaign root is
bound to one exact commit and one exact kernel identity; preparing from a tree
with uncommitted edits would produce evidence nobody can reproduce. Commit
first.

`reconstruct_v67.py` **refuses a `requirements.txt` that does not match the
reconstruction manifest**. The pins are hashed. Appending even a comment breaks
it, deliberately — document optional dependencies in a README instead of
editing the pinned file.

Usage is in `../CURUNIR_V6_8_PILOT_PROTOCOL.md` and
`../CURUNIR_RECONSTRUCTION.md`.

# Curunír V6.7 clean-checkout reconstruction

Curunír V6.7 reconstructs from a committed repository tree plus one explicit
external source dependency: the inherited `argus` kernel. The kernel is not
tracked by this repository and the repository contains no download locator for
it. A reconstruction is valid only when the operator supplies the snapshot
whose complete Python-source tree hash is pinned in
`CURUNIR_V6_7_RECONSTRUCTION.json`.

This is an explicit external boundary, not a fallback to a developer checkout.
`tools/reconstruct_v67.py` requires `--kernel`, rejects a missing or mismatched
snapshot, creates a tree using `git archive` of the requested commit, mounts
only the verified kernel source, and runs an isolated create, append, backup,
restore, and replay smoke test. It never searches the home directory or another
worktree for source.

## Supported reconstruction environment

- CPython 3.12 on Linux x86_64 with glibc 2.28 or newer.
- Python packages exactly pinned by `requirements.txt`. The neural suites use
  the hash-pinned official PyTorch 2.8.0 CPU wheel; CUDA is not required.
- `pdftotext` from poppler-utils is an optional live extraction dependency. Its
  absence is represented as a bounded processing failure, not as evidence
  absence.
- Playwright's Chromium runtime is needed only for browser-marked tests.
- PostgreSQL is needed only for the legacy DB-marked tests. File-backed Curunír
  operation, backup, restore, and replay do not need it.

## Canonical reconstruction

From `argus_demo/` in a clean checkout:

```bash
uv run --python 3.12 --with-requirements requirements.txt \
  python tools/reconstruct_v67.py \
  --kernel /path/to/pinned/argus \
  --output /tmp/curunir-v67-reconstructed
```

The output directory must not exist. The command fails closed if the commit
cannot be archived, the requirements or kernel identities differ, tracked
state unexpectedly contains the external kernel, or isolated replay fails.

For browser validation, install the runtime named by the tracked Playwright
version before running the terminal suite:

```bash
PLAYWRIGHT_BROWSERS_PATH=/path/to/playwright-cache \
uv run --python 3.12 --with-requirements requirements.txt \
  playwright install chromium
```

The precise manifest and its hashes are machine-readable in
`CURUNIR_V6_7_RECONSTRUCTION.json`.

## Terminal V6.7 validation

After installing the pinned Chromium runtime, the complete deterministic gate
is:

```bash
PLAYWRIGHT_BROWSERS_PATH=/path/to/playwright-cache \
uv run --python 3.12 --with-requirements requirements.txt \
  python tools/validate_v67.py \
  --kernel /path/to/pinned/argus \
  --report /tmp/curunir-v67-terminal.json
```

The validator requires a clean tracked tree, verifies every harness and kernel
hash, runs the focused V6.7 and Curunír product-plane suites, reconstructs the
committed checkout, then runs the complete repository suite. The full suite is
not called green while accepted historical failures remain. Instead, the
validator compares every nonpassing node ID against the exact V6.6 set in
`CURUNIR_V6_7_BASELINE_NONPASSING.json`; a new node, increased skip count, or
collection shrinkage fails the command. A successful current run therefore
reports `PASS_WITH_ACCEPTED_BASELINE_RESIDUALS`, not an unqualified pass.

# Housekeeping — how this repository stays clean

This is the standing rule set for what lives where, what may be deleted, and
how anything superseded leaves. Each plane has a `MANIFEST.md` that applies
these rules to its own directory. If a file does not fit any row of its plane's
manifest, it does not belong in that plane.

## 1. One product, one external dependency, nothing between them

| Directory | Owns | Never contains |
|---|---|---|
| `curunir/` | the product (`curunir_*`), its tests, tools, contracts and ledgers, the virtualenv | kernel source, research code, trained artifacts, campaign outputs |
| `kernel/` | the documentation of the pinned ARGUS kernel; the untracked tree, tarball and `schema.sql` beside it | anything Curunír-specific, anything edited |

Imports go one way: `curunir` → `kernel`. The ARGUS research lines (neural
extractor, codec capsules) and netwatch live in the Argus repository (`/home/moloch/Argus`, called Saulot until 2026-09-02), not
here. If code needs them, that is a design question, not an import.

Outside those two only these are allowed at the repository root:
`README.md`, `NAVIGATION.md`, `HOUSEKEEPING.md`, `.gitignore`,
`curunir_v68_runs/` (pilot evidence) and `.claude/`. **No loose files at the
root.** A stray patch, log, audit or snapshot at the root is a bug.

## 2. Tracked vs. untracked is a decision, not an accident

- Tracked: source, tests, contracts, ledgers, READMEs, small fixtures, the
  Saulot commit map. Nothing over a few megabytes; this repository is meant to
  push to GitHub without LFS.
- Untracked by policy (listed in `.gitignore` / `.git/info/exclude`): the
  kernel tree, tarball and `schema.sql` (external, hash-verified), the
  virtualenv, `curunir/conftest.py` and `pytest.ini`, mission roots, pilot
  evidence, `docs/` and `research/` history.
- Nothing is "untracked because nobody added it". If a file has been untracked
  for more than one campaign, either add it or move it to where untracked
  things live (its plane's `artifacts/`, `docs/`, or out of the repo).
- `curunir/requirements.txt` is hash-locked by the reconstruction manifest.
  Never edit it. Optional dependencies are documented in a README.

## 3. Superseded things leave, with a ledger line

A thing is **superseded** when a newer version exists and nothing frozen
points at the old one. Then it is deleted, not kept "just in case":

| Kind | Rule |
|---|---|
| Git worktrees | one per live line of work. When its branch is merged or abandoned, `git worktree remove` it the same day. Branches stay; checkouts do not. |
| Snapshot tarballs | the kernel tarball is the only one that belongs here, and it is untracked. Anything else is deleted. |
| Campaign artifact roots | keep what a ledger, qualification or handoff cites. Untracked campaign output whose campaign is closed is deleted after its verdict is recorded in the nearest ledger. |
| Rehearsal mission roots | `curunir_v68_runs/` is the exception: never edit or delete a prepared root, even a superseded rehearsal, because each is bound to one commit and one kernel identity and is evidence only in its original bytes. |
| Byte-code and caches | `__pycache__`, `.pytest_cache`, `.mypy_cache`: delete freely, never commit. |
| Handoff and audit prose at the root | belongs in the plane it is about, or in a ledger. Delete the copy at the root. |
| Duplicate copies of a tracked file | the working tree has one copy. If a byte-identical copy exists elsewhere in the repo, delete it. |

Every deletion of something that was ever load-bearing gets a line in the
nearest ledger (`curunir/CURUNIR_V6_9_EXCISION.json` style), naming the git
tag or commit it can be recovered from.
The tests that asserted over it are deleted in the same change, with the same
ledger line, so the suite never carries dead assertions silently.

## 4. Frozen documents change only by ledgered revision

Anything marked frozen or hash-pinned (`CURUNIR_V6_7_RECONSTRUCTION.json`,
`*_REPOSITORY_TRUTH.json`, `CURUNIR_V6_8_PILOT_PROTOCOL.md`,
`CURUNIR_V6_8_QUALIFICATION.json`, `CURUNIR_V6_9_EXCISION.json`) keeps its
bytes between revisions. A revision is a change-control event and leaves three
traces: a `revised_<date>` block in the file naming the sha256 it supersedes,
an `authority_supersession` entry in the qualification contract when any of
the five V6.8 authority files changed (so existing campaign roots still
verify), and a section in `curunir/CURUNIR_DOCUMENTS.md`. Pilot evidence under
`curunir_v68_runs/` is never revised.

## 5. The kernel is gold

`kernel/argus/` is verified against `4c173df7…` by the reconstruction test, the
V6.7 validator and the V6.8 harness. Nobody edits it, adds a file to it, or
symlinks it. If the kernel ever has to change, that is a new pinned snapshot
with a new tarball, a new hash in a new reconstruction manifest, and a ledger
entry — never an in-place edit.

## 6. Naming

- Directories: lowercase, underscores for Python packages
  (`curunir_operational`), hyphens or underscores for everything else, no
  spaces, no dates in a directory that is meant to stay.
- Anything dated (`*_20260725`) is by definition a campaign output and lives
  under an `artifacts/` directory, never at a plane root.
- Version words in a file name (`v0`, `v5_8_1`) mean the file is a snapshot.
  A snapshot has a newer sibling or a frozen citer; otherwise see §3.

## 7. Before every commit

1. `git status --porcelain | grep '^??'` — every untracked path is either in
   an ignore list on purpose or about to be added. No third state.
2. No files at the repository root beyond §1.
3. `curunir/MANIFEST.md` and `kernel/MANIFEST.md` still describe what is
   actually there. If you added a directory, add its row.
4. `git worktree list` shows only live work.
5. The product suite passes from `curunir/`. The V6.8 harness tests need a
   clean tree: verify them from a throwaway `git worktree add` of HEAD.

## 8. Quarterly (or whenever the disk complains)

- Run §3 over `curunir/docs/` and `curunir/research/`. Delete superseded,
  record what left.
- Verify the kernel hash and that `kernel/argus_kernel_pinned_4c173df7.tar.gz`
  still unpacks to it.
- Rebuild the virtualenv from `curunir/requirements.txt` rather than
  accumulating packages in it.

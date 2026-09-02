# Housekeeping — how this repository stays clean

This is the standing rule set for what lives where, what may be deleted, and
how anything superseded leaves. Each plane has a `MANIFEST.md` that applies
these rules to its own directory. If a file does not fit any row of its plane's
manifest, it does not belong in that plane.

## 1. The three planes, and nothing between them

| Plane | Owns | Never contains |
|---|---|---|
| `curunir/` | the product (`curunir_*`), its tests, tools, contracts and ledgers, the one virtualenv | kernel source, neural/capsule code, research artifacts, campaign outputs |
| `kernel/` | the pinned ARGUS tree, its backup tarball, the Postgres spine and demo kit, the ARGUS research tests | anything Curunír-specific, anything trained |
| `capsules/` | `argus_neural`, `argus_capsules`, their artifacts and tests, the frozen capsule tarballs | product code, kernel source |

Cross-plane imports go one way only: `curunir` → `kernel`, `capsules` →
`kernel`. Nothing imports from `curunir` except `curunir`. Nothing imports from
`capsules` except `capsules`. A test lives in the plane whose code it imports;
if it imports from two planes, it lives with the higher one (`curunir` >
`capsules` > `kernel`) and reaches down through `../kernel/...` explicitly.

Outside the planes only these are allowed at the repository root:
`README.md`, `NAVIGATION.md`, `HOUSEKEEPING.md`, `.gitignore`,
`curunir_v68_runs/` (pilot evidence), `02_CONTRACT/` and `.worktrees/`
(netwatch, out of scope for these rules), and `.claude/`. **No loose files at
the root.** A stray patch, log, audit or snapshot at the root is a bug.

## 2. Tracked vs. untracked is a decision, not an accident

- Tracked: source, tests, contracts, ledgers, READMEs, reports and cards,
  small fixtures, the tiny256 capsule tarball (already in history).
- Untracked by policy (listed in `.gitignore` / `.git/info/exclude`): the
  kernel tree (mounted, hash-verified), virtualenvs, model weights and datasets,
  corpora, content stores, inboxes, mission roots, pilot evidence, `docs/` and
  `research/` history, and `.env`.
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
| Snapshot tarballs | keep the newest per subject and any tarball a frozen document names. Delete strict subsets (the 22:46 report-surface snapshot was a subset of 23:08 and went). |
| Campaign artifact roots | keep what a ledger, qualification or handoff cites. Untracked campaign output whose campaign is closed is deleted after its verdict is recorded in the plane's `artifacts/README.md`. |
| Rehearsal mission roots | `curunir_v68_runs/` is the exception: never edit or delete a prepared root, even a superseded rehearsal, because each is bound to one commit and one kernel identity and is evidence only in its original bytes. |
| Byte-code and caches | `__pycache__`, `.pytest_cache`, `.mypy_cache`: delete freely, never commit. |
| Handoff and audit prose at the root | belongs in the plane it is about, or in a ledger. Delete the copy at the root. |
| Duplicate copies of a tracked file | the working tree has one copy. If a byte-identical copy exists elsewhere in the repo, delete it. |

Every deletion of something that was ever load-bearing gets a line in the
nearest ledger (`curunir/CURUNIR_V6_9_EXCISION.json` style, or the plane's
`artifacts/README.md`), naming the git tag or commit it can be recovered from.
The tests that asserted over it are deleted in the same change, with the same
ledger line, so the suite never carries dead assertions silently.

## 4. Frozen documents are read, not edited

Anything marked frozen or hash-pinned (`CURUNIR_V6_7_RECONSTRUCTION.json`,
`*_REPOSITORY_TRUTH.json`, `CURUNIR_V6_8_PILOT_PROTOCOL.md`,
`CURUNIR_V6_8_QUALIFICATION.json`, `CURUNIR_V6_9_EXCISION.json`) keeps its
bytes. When the layout changes, the *code* that consumes them changes
(environment variable, repo-relative default) and a note goes into
`curunir/CURUNIR_DOCUMENTS.md`. This is why those files still say `argus_demo`.

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
3. Each plane's `MANIFEST.md` still describes what is actually there. If you
   added a directory, add its row.
4. `git worktree list` shows only live work.
5. The product suite passes from `curunir/`; the sibling planes at least
   collect (`pytest --co -q`).

## 8. Quarterly (or whenever the disk complains)

- Run §3 over each plane's `artifacts/` and over `kernel/docs/`,
  `curunir/docs/`, `curunir/research/`. Delete superseded, record what left.
- Verify the kernel hash and that `kernel/argus_kernel_pinned_4c173df7.tar.gz`
  still unpacks to it.
- Rebuild the virtualenv from `curunir/requirements.txt` rather than
  accumulating packages in it.

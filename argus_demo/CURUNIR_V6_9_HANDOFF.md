# Curunír V6.9 — handoff

**Date:** 2026-08-26 · **Branch:** `feature/curunir-v69-excision`
**Worktree:** `/home/moloch/Saulot/.worktrees/curunir-v69`
**Head:** `2bba6a8` · 7 commits ahead of `feature/curunir-v68-terminal-validation`
**Nothing is pushed.** Nothing is merged.

---

## 1. Read this first

**`feature/curunir-v67-hardening` is a dead lineage.** It is not an ancestor of
V6.8 and it still carries a real `test_cross_workbench_access_no_leakage`
failure. The main working tree at `/home/moloch/Saulot` is checked out on it.
Do not work there. The live product is the v69 worktree above.

Orientation, in order: `../README.md` → `../NAVIGATION.md` →
`CURUNIR_DOCUMENTS.md` → `OVERVIEW.md`.

## 2. State

| | |
|---|---|
| Product plane | **774 passing**, 5 skipped |
| Browser E2E | **14/14 passing** (Playwright Chromium now installed) |
| Known failures | 2, both pre-existing and understood — see §6 |
| Product size | ~33k lines across 6 packages, ~36k lines of tests |
| Repo size | ~28 GB → **15 GB** |

## 3. What this session did

**Excised the research-campaign tree** (`3f194d5`, `75ab5ee`).
`curunir_operational` went 67,314 → 10,886 lines; the suite 5,699 → 1,493
nodes. 4,206 nodes removed, **all inside the declared removal set, zero new
nodes, zero outcome-kind changes**. Ledger: `CURUNIR_V6_9_EXCISION.json`.
Everything removed is at tag `archive/curunir-campaign-tree-v5x`.

The live product reached that tree through **two function-local imports** in
`curunir_semantic/extract.py` that a module-level grep does not see. The
reachable 316 of 1,808 lines were lifted to
`curunir_semantic/provenance_capture.py` and proven equivalent by differential
execution (58 documents, 116 invocations, 493 observations, **0 mismatches**).
That module holds the multilingual provenance extractor — CELEX/ELI/Amtsblatt/
Journal officiel identifiers — and is the most "European" asset in the repo.

**Wired real model providers** (`ad35960`). `anthropic` and `openai` behind the
existing governed seam, plus a deterministic offline backend. The
structured-output schema is *generated* from `CANDIDATE_BINDING_KEYS` and the
`contracts` enums, so provider constraint and human review path cannot drift. A
provider may cite only identifiers it was shown. Credentials delegate to each
SDK's own chain. Both SDKs optional and lazily imported; **neither is installed**.
`POST /api/commands/proposals`, `GET /api/model/status`.

**Documented the tree** (`079b99b`, `0a3400c`). Root `README.md`,
`NAVIGATION.md`, `OVERVIEW.md`, `CURUNIR_DOCUMENTS.md`, and a README at every
node.

**Rebuilt the workbench surface** (`e657a44`, `2bba6a8`). See §5.

## 4. Traps that cost time — do not rediscover these

- **`requirements.txt` is tracked and hash-pinned** by the V6.7 reconstruction
  manifest. Appending even a *comment* fails `test_clean_reconstruction`.
  Recover with `git checkout -- requirements.txt`. Document optional deps in a
  README instead.
- **Mount the kernel as a real directory, not a symlink.**
  `.git/info/exclude` has a trailing-slash pattern that ignores directories but
  not symlinks, and an untracked symlink makes the V6.8 harness refuse the
  checkout.
- **`tests/test_curunir_v68.py` refuses a dirty checkout by design.** Commit
  first. `tests/v67/test_clean_reconstruction.py` needs `CURUNIR_ARGUS_KERNEL`
  or the mount.
- **`node --check` proves nothing about a UI.** A patch left a dangling
  `window.` before an inserted block; syntax checking passed and every browser
  test failed at login. Load it in Chromium.
- **Never point a dev server at a V6.8 mission root.** Copy it. Those roots are
  frozen evidence bound to one commit and one kernel identity.

## 5. The workbench

One UI, at `/`. The shipped app was **restyled in place**, not replaced: a
rewrite failed 7 browser journeys that assert real properties (separation of
duties, access filtering, descent to an exact anchor), and the only way to make
a rewrite pass would have been to weaken them.

Design is "Quiet Instrument" — see `curunir_workbench/static/README.md`. Value
is the headline, epistemic status is a word, identifiers live behind **Auditor
mode** (`a`); theme is `t`.

`refLink()` was the identifier leak — it labelled links with `clip(id, 28)`.
Fixed at source. The overview header no longer opens with `store … · state …`.

**Not done, in priority order:**

1. **Dossier authoring and the approval path.** This is where the product's
   actual claim lives (a `SUPPORTED` sentence *is* its citation) and it is the
   last mile that forced both V6.8 pilots into `curl`.
2. **`views3.js` silently falls back to an unsigned `POST /approve`** when
   WebCrypto is unavailable. V6.8 does not accept that. Real defect.
3. The nav is still a family browser over 24 record types, not the mission
   workflow the pilots needed. `/claims` is the only surface built to
   `curunir_v68_runs/CURUNIR_WORKBENCH_UI_FABLE_BRIEF.md`.

## 6. The two known failures

- `test_operational_v2_integration::test_live_vs_synthetic_distinct` —
  pre-existing; needs untracked live capture fixtures.
- `test_curunir_v68::test_required_m3_review_paths_exist_on_existing_workbench`
  — the dirty-checkout guard. Passes on a clean tree.

## 7. Everything else that is open

- **M2 is `NOT_ACHIEVED`** on backend basis-closure defects: a manifestation id
  aliased as `ingestion`/`object_version`, and SUPERSEDED records pulled into
  the approval closure. **M3 was prepared and never run.**
- **No EU source is wired.** GLEIF (global), SEC EDGAR (US), Wikidata, Wayback.
  EUR-Lex, TED, a national register and the consolidated sanctions list are
  days of work, and `provenance_capture.py` already recognises the identifiers
  they emit. This is the cheapest credibility win available.
- **Not deleted, awaiting a decision:** `artifacts/private_capsules` (4.9 GB of
  trained checkpoints) and ~350 MB of root tarballs. Two
  `argus_report_surface_v0` tarballs are 92 MB each, 22 minutes apart — one is
  almost certainly redundant.
- **The kernel had no backup.** The pinned tree existed only as the live
  `argus_demo/argus/`. `argus_kernel_pinned_4c173df7.tar.gz` now exists at the
  repo root and verifies against the pin. Keep it.

## 8. Running it

```bash
cd /home/moloch/Saulot/.worktrees/curunir-v69/argus_demo
export PYTHONPATH="$PWD"
VENV=/home/moloch/Saulot/argus_demo/.venv/bin/python   # the worktree has no .venv

$VENV -m pytest tests/ -q -p no:cacheprovider          # the suite

# the workbench, against a COPY of a mission root
CURUNIR_MISSION_ROOT=/path/to/copy CURUNIR_ACTORS=/path/to/copy/actors.json \
  $VENV -m uvicorn --factory --port 8142 curunir_workbench.server:app
```

# CURUNIR V6.7 — HANDOFF

**Date:** 2026-08-18 · **HEAD:** `06060c0` · **Branch:** `main`
**Verdict:** `READY_PENDING_CONFIRMATORY_ADVERSARIAL_REVIEW_OF_ROOT_CAUSE_DIFF`
**NOT MERGED.** Do not merge until the condition below is met.

---

## 1. The one thing to do next (the stopping condition)

The four root-cause chokepoints were repaired this round (commit `06060c0`) after a
fresh confirmatory attack broke the first attempt (`9d0760d`). **That repaired diff
has itself not yet been independently attacked.** Per the convergence rule the
operator (Jan) set:

> Give the diff to FRESH reviewers to attack the claim that the four chokepoints
> structurally close the defect classes. Look for bypasses, incomplete mediation,
> repair-induced variants, composition failures, and cases where the tests encode a
> narrower invariant than the class requires. **Do NOT pre-decide that this is the
> final review.** If it survives → that is the missing evidence → merge. If it finds
> a substantive defect → repair (self-reviewing the sibling first) and continue
> **until fresh review stops materially changing the implementation.**

**Immediate action:** dispatch two fresh `opus-specialist` reviewers, READ-ONLY,
attacking the current state of the chokepoints (the diff `git show 9d0760d 06060c0`),
split A = marking/gate, B = durability, with the attack list in §3. When a reviewer
returns `DIFF_SURVIVES` for both domains, recommend `READY_TO_MERGE_TO_MAIN`. On
`DIFF_BROKEN`, repair + re-attack.

The trap already sprung TWICE: stopping right after the load-bearing repair, and
writing class-level tests that assert the invariant the code already satisfies. Both
reviewers this round found the fix applied to one site and skipped its sibling seven
lines below. Attack accordingly.

---

## 2. What the four chokepoints ARE now (what to attack)

All in commit `06060c0` on top of `9d0760d`:

1. **Derived-record marking floor** — `curunir_analytic/substrate.py`:
   `record_transition` and `marked_for_subject` floor a transition/review/alert on
   its SUBJECT's current marking **AND on every `evidence_ref` resolved to its
   current marking** (`_reference_markings` over claims + all `ANALYTIC_ID_FIELDS`
   families + activity/execution). Rule: "cite what you quote" → floor is automatic.
   Leaking sites fixed to cite: `indicators.py` INDICATOR_FIRED, `forecasts.py`
   PROBABILITY_UPDATED + EXPECTED_NOT_OBSERVED, `impact.py` ASSUMPTION_INVALIDATED;
   `indicators.py` `_fold` references the indicator by scrubbed id, not its text.
   Property lock: `test_analytic_warning.py::test_no_derived_record_underclassifies_anything_it_references`
   asserts `marking ≥ join(subject, ALL references)` over the SHIPPED `check_indicators`
   /`refresh_warnings` paths.
2. **Fail-closed basis gate** — `curunir_workbench/reports.py::_raw_hidden_basis_concerns`
   (called by `approve_report`): a cited basis whose CURRENT claim-state or OPEN
   review the approver cannot `can_view` blocks approval. Compares the CURRENT
   state's visibility, not "any state ever". Lock:
   `test_workbench_reports.py::test_hidden_claim_state_on_cited_basis_blocks_approval`
   (includes the benign-visible-then-hidden-retraction masking case).
3. **Durability primitives** — `curunir_operational/store.py` + `delta.py`:
   `put_payload` writes its temp OUTSIDE `payload_dir`, is crash-atomic (fsync+replace)
   + self-repairing; `get_payload` fails loud on corrupt/dir; `export_to` copies only
   64-hex names; `curunir_workbench/provenance.py` catches the loud `StoreError` and
   falls back to verified custody; the delta bundle detects payloads family-agnostically
   (`_scan_digests`, keys included) and installs them PER APPLIED EVENT after the
   divergence check. Locks: `test_store_durability_review.py` (put_payload repair,
   export-ignores-stray) + `test_operational_v2_fabric.py` (delta suite).
4. **Two rules** — `sessions.py`/`server.py`: `/api/auth/challenge` cap-eviction keyed
   to the bearer principal; `projections.py` id-scrub covers `[:20]`/`[:12]`.
   **Confirmed by round 24 attack; held.**

---

## 3. Attack list for the fresh review (the operator's, carried forward)

- **Ck1 complete mediation:** is `record_transition` the ONLY transition writer? Does
  a derived record (transition/review/**alert**) quote a NON-subject, NON-cited object
  anywhere? Sweep `changes.py`/`stakeholders.py`/`themes.py`/`narratives.py` derived
  records too (NOT audited this round — see §4). Drive the shipped background pass with
  SPECIAL indicator + forecast + warning + assumption and prove no uncleared leak.
- **Ck1 repair-induced:** does resolving `evidence_refs` per-transition over all
  families create a perf/quadratic problem on the background pass (check the endurance
  test time)? Does `_reference_markings` mis-resolve (id collisions) or over-classify?
- **Ck2 bypass/oracle:** can the current-state check still fail open (a RESOLVED→reopened
  race, a review keyed wrong, a basis ref that is an object id not a claim id)? Does the
  refusal disclose more than the dissent gate (B2, accepted as fail-closed — confirm the
  scope is the report's own basis, not attacker-chosen)?
- **Ck3 compositions:** interrupted-write / corrupt-existing / concurrent put_payload;
  a bundle that OMITS a referenced payload (B-4, see §4); both import families through
  equivalent logic; export under a concurrent writer.

---

## 4. Known residuals — hand to the fresh review, not fixed

- **B-4:** `import_delta_bundle`/`import_from` cannot prove referential completeness
  generically (a 64-hex string is a ref or a hash — indistinguishable). An HONEST bundle
  (built by `_referenced_payloads`) is complete; a HOSTILE omission surfaces at read as
  `FileNotFoundError` from `get_payload`. Decide whether to type it or accept.
- **Semantic-plane derived records** (`curunir_semantic/changes.py:340` `ClaimStateRecord`
  / `ReviewItem`): round-23 reviewer B noted these floor correctly on the change, but they
  were NOT re-attacked under the round-24 confirmatory lens. Sweep them.
- **B2 disclosure:** the basis gate necessarily signals "a blocker you cannot see exists"
  — inherent to fail-closed, same as the dissent gate. Accepted; confirm scope.
- **Analogue-over-restricted-episode** and **provider `inputs`-vs-`input_refs`** — latent
  (unwired seams), documented in `CURUNIR_V6_7_SECURITY.md`.

---

## 5. Campaign context (24 rounds)

- The **enforcement core is unbroken** across ~7 independent reviewer passes: bearer/
  actor-kind/releasability fail-closed, four-eyes on the plain AND signed paths, the
  Ed25519 refusal order, key no-write-down, the marking WRITE-side floors
  (`append_version`), torn-tail recovery, SSRF/egress. Do not re-audit these unless a
  diff interacts with them.
- The recurring failure was **classes closed per-instance**, not per-chokepoint. The
  poison-value/serialization class and its non-finite twin were closed at the
  `canonical_line` seam (rounds 12–22). This round closed the **derived-record marking**,
  **fail-open-gate**, and **payload-durability** classes at chokepoints — *correctly* only
  after the confirmatory attack broke the first attempt.
- **Full findings ledger:** `CURUNIR_V6_7_FINDINGS.md` (rounds 1–24). Security posture &
  documented limitations: `CURUNIR_V6_7_SECURITY.md`. Rebuild instructions:
  `CURUNIR_RECONSTRUCTION.md`.

## 6. Commit chain (this tranche, on `main`)

Base `9705315` → … prior tranche … → serialization-class rounds (`fc909f6`,`193af2c`,
`8af85a3`,`b8213d3`,`04dbc34`,`24f97f4`,`72b3696`) → root-cause pass `9d0760d` →
**repair `06060c0` (HEAD)**. Nothing is on a feature branch; all commits are `main`.

## 7. Verification state

- Broad subsystem sweep after the repair: **5095 passed / 0 regressions**.
- Full definitive suite: running at handoff time (task `bpoadzs1b`); expected result is
  the 2 known-unrelated capsule/neural failures only
  (`test_no_symbolic_inverse`, `test_capsule_package_has_no_forbidden_inverse_or_payload_decoder`
  — Jan's separate work, NEVER touch), everything else green, 790 Postgres skips.
  **CONFIRM this before the fresh review; if a 3rd failure appears it is a repair-round
  regression to fix first** (this happened once — `test_gate11_integrity_repairs` — the
  broad-sweep globs miss differently-named tests, so the FULL suite is the gate).
- What is NOT verified: the repaired chokepoints have **not been independently attacked**
  — that is exactly §1.

## 8. Standing constraints (do not violate)

- Commit ONLY `curunir_*` packages, `tests/`, and `CURUNIR_*` docs, with EXPLICIT
  `git add` of named files (never `git add -A`). Verify scope every commit.
- NEVER modify, stage, or commit: the `argus/` kernel (git-exclude-listed, untracked —
  read-only), or Jan's unrelated working-tree changes (`argus_capsules/*`,
  `argus_neural/*`, `artifacts/*`, and the pre-modified `tests/conftest.py`,
  `test_actions.py`, `test_case_assembly.py`, `test_claim_relation_proposals.py`,
  `test_technical_relation_proposals.py`).
- venv dependency additions need Jan's sign-off.
- Environment: `./.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. Postgres
  (port 5544) is down at handoff → 790 skips are expected, not failures. This tranche
  has ZERO DB-gated tests.
- NEVER weaken a test, gate, or invariant to force convergence. Preserve `DIFF_BROKEN`
  honestly.

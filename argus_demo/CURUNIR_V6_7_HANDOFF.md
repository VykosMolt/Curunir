# CURUNIR V6.7 — HANDOFF

**Date:** 2026-08-19 · **Branch:** `feature/curunir-v67-hardening`
**Verdict:** `NOT_CONVERGED` — Round 33 whole-tranche review returned `DIFF_BROKEN`. The repair is on HEAD. Do not merge. Next: two fresh whole-tranche reviewers (Round 34).
**NOT MERGED.** Do not merge until fresh review of the **whole V6.7 tranche**
returns `DIFF_SURVIVES` on both domains.

---

## 1. The one thing to do next (the stopping condition)

Round 26 independently attacked `5b5125a` (the Round-25 repair) across the
**whole tranche** and **broke it** (two CRITICAL on Ck2/collect; three MAJOR
on export skip / dest-symlink / delta partial-apply). Those classes were
closed at the chokepoints in the commit that lands this handoff. **That
repair has itself not been independently attacked.**

> Give the current tree to FRESH reviewers. Attack the claim that the
> V6.7 classes (marking floor, fail-closed gates, payload durability,
> serialization totality, identity/enforcement, egress) stay closed,
> including bypasses, incomplete mediation, repair-induced variants,
> composition failures, and tests that encode a narrower invariant than
> the class requires. **Do NOT pre-decide that this is the final review.**
> If it survives → that is the missing evidence → merge. If it finds a
> substantive defect → repair (self-review the sibling first) and continue
> **until fresh review stops materially changing the implementation.**

The trap has sprung three times: stopping right after the load-bearing
repair; writing class-level tests that assert the invariant the code
already satisfies; applying a floor to transitions and skipping the
version / the other import family.

---

## 2. What changed in Rounds 25–26 (what to attack first)

1. **Shared reference-marking resolver** — `resolve_reference_markings` in
   `curunir_analytic/substrate.py` now covers claims, all `ANALYTIC_ID_FIELDS`,
   observations, documents, changes, claim-state, hypotheses, discriminators,
   routes, review items, activity, execution, manifestations, object versions,
   requirements. `forecasts._ref_markings` delegates to it. Attack: a cited
   family it still misses; id-collision first-match; quote-without-cite.
2. **Cite-what-you-quote on versions, not just transitions** —
   `APPLY_PROBABILITY` / `_fire` / `INDICATOR_FIRED` / `retrieve_analogues` /
   collection-need text no longer embed foreign description/rationale/value.
   `append_version` floors `historical_analogue` on the embedded episode;
   it still does **not** floor a forecast on `indicator_ids` (A4).
3. **Ck2 related-claim walk** — `_related_claim_ids` in
   `curunir_workbench/reports.py` follows observation → claim and
   manifestation → observation → claim. Attack: other legal `basis_refs`
   families (`analytic_forecast`, hypothesis, …) whose hidden claim-state
   still fails open; signed vs plain path.
4. **Export hashes; import is staging-atomic** — `export_to` copies through
   `get_payload` (torn → `StoreError` at backup time). Round 26: a 64-hex
   symlink/dir/fifo is a `StoreError` (not a skip); dest-side writes refuse
   symlinks; `put_payload` replaces a symlink slot; `import_delta_bundle`
   preflights the whole suffix before planting payloads; torn `store_meta`
   is not a store (retry allowed). Attack the next sibling of each.

5. **Ck2 walks every validate_report family** — `_related_claim_ids` follows
   material claims, embedded analytic ids, and reverse edges (objective ←
   assumption/path). Attack any cited family still fail-open.

6. **Collection needs cite ids only** — theme/narrative/forecast/indicator/
   interest/edge-note text is not interpolated into discriminator questions.

Locks added: `test_apply_probability_does_not_embed_indicator_rationale`,
`test_fire_does_not_embed_observation_value`,
`test_retrieve_analogues_cites_episode_not_its_title`,
`test_hidden_retraction_via_cited_observation_blocks_approval`,
`test_export_refuses_a_torn_payload`,
`test_import_retries_over_a_dest_without_store_meta`.
Property test now indexes observations.

---

## 3. Whole-tranche attack list (Round 26)

Do not only re-run the Round-25 probes. Sweep:

- Marking / derived records / `append_version` / refreshers / authoring
- Fail-closed gates (`approve_report`, dissent, four-eyes, signed path)
- Payload durability, export/import/delta, torn tail, crash leftover
- Serialization totality (`canonical_line`, `_scrub_output`, `_deep_render_safe`)
- Identity (enroll, challenge eviction, Ed25519 order, target binding)
- SSRF / egress / RSS DOCTYPE / resource caps
- Semantic-plane `changes.py` / `pipeline.py` (held in R25-A; re-check
  under the new resolver)
- Unswept halves of this repair (`collect.py` was self-reviewed to id-only;
  `explain_analogue` still *returns* an episode title to a caller who can
  already read the store — not a persisted derived record)

Do **not** re-audit the held enforcement core unless this diff interacts:
bearer/actor-kind/releasability fail-closed, four-eyes on both paths,
Ed25519 refusal order, key no-write-down, torn-tail recovery, SSRF
byte-level blocks already enumerated.

---

## 4. Known residuals (hand to the review, not fixed)

- **B-4:** generic referential completeness of a 64-hex string is
  undecidable. Honest builder is complete; hostile omission is fail-closed
  at `get_payload`. Accepted LIMITATION unless a silent-wrong-evidence path
  is shown.
- **B2 disclosure:** a blocking gate signals "a blocker you cannot see
  exists". Accepted if the scope is the report's own basis.
- **Analogue-over-restricted-episode** as a *record* is now floored
  (R25A-3). `explain_analogue` still surfaces the episode title to a
  caller holding the raw store.
- **Provider `inputs` vs `input_refs`:** still unwired; documented.
- A hand-built dest with `store_meta`+events and empty `payloads/` still
  opens (legacy / operator). The new `import_from` cannot produce it.

---

## 5. Campaign context

24 prior rounds on `main` lineage `9705315` → … → `9d0760d` → `06060c0` →
docs `f16db69`, then Round 25 on `feature/curunir-v67-hardening`. Full
ledger: `CURUNIR_V6_7_FINDINGS.md`. Security posture:
`CURUNIR_V6_7_SECURITY.md`. Rebuild: `CURUNIR_RECONSTRUCTION.md`.

## 6. Verification state

- Chokepoint + sibling sweep after the Round-25 repair: **243 passed**.
- Original R25A-1/2/3 probes now print `RESULT HELD`; R25A-4 observation
  cite now raises the hidden-basis refusal; R25B-1 `export_to` raises
  `StoreError` on a torn slot.
- Full definitive suite last run before the repair: **6509 passed**, 790
  Postgres skips (port 5544 down), 2 known capsule/neural failures (Jan —
  NEVER touch), 14 Playwright ERRORs (Chromium binary missing, not a
  product regression). Re-run the full suite after this commit before
  treating merge as unblocked.
- Postgres down → 790 skips expected. This tranche has ZERO DB-gated tests.

## 7. Standing constraints (do not violate)

- Commit ONLY `curunir_*` packages, `tests/`, and `CURUNIR_*` docs, with
  EXPLICIT `git add` of named files (never `git add -A`).
- NEVER modify, stage, or commit: the `argus/` kernel, or Jan's unrelated
  working-tree changes (`argus_capsules/*`, `argus_neural/*`, `artifacts/*`,
  and the pre-modified `tests/conftest.py`, `test_actions.py`,
  `test_case_assembly.py`, `test_claim_relation_proposals.py`,
  `test_technical_relation_proposals.py`).
- NEVER weaken a test, gate, or invariant to force convergence.
- Environment: `./.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

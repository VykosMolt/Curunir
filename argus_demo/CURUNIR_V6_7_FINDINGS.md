# Curunír V6.7 — Adversarial Finding Ledger

Substantiated findings and their closure. Reviewers: self-reproduction
(engineer) and an independent Opus-5 adversarial pass over the marking + SSRF
change set. Each entry: severity, invariant, reachable path, reproducer, repair.

The review model mix: broad self-attack during implementation; two deep Opus-5
rounds on the composed marking+SSRF diff (descriptive count, no predetermined
target). Round 1: SSRF sound; the marking generalization's one omitted family
(the analytic refreshers) surfaced as a CRITICAL, repaired at a shared
chokepoint. Round 2: verified that closure across all six families with no
chokepoint bypass, and caught a repair-induced write-down in the round-1 F-03
seal (F-03b), repaired with a deny-all seal + own-marking floor + a
cross-axis property lock. Converged after round 2: no open Critical/Major
defect on any axis.

---

## R-01 — Forecast-resolution value leak (the named V6.6 residual)
- **Severity:** MAJOR (unauthorized disclosure of a restricted claim value)
- **Invariant:** a value cannot move from a more restricted object into a less
  restricted derived record because the transition ran in a lower context.
- **Path:** `process_fabric_changes` → analytic propagation → `refresh_forecast`
  → `try_machine_resolution`: a CLAIM_PREDICATE resolution embedded the resolving
  claim's literal value into the forecast version's `change_reason` and the
  RESOLVED_* transition detail, both stamped `ctx.marking` (PUBLIC on the watch
  path).
- **Reproducer:** `tests/test_forecast_resolution_marking_leak.py` — a PUBLIC
  forecast resolved against a SPECIAL claim; CTX_B read `reads 'CLASSIFIEDXYZZY'`
  from a PUBLIC transition.
- **Repair:** resolution/refresh/fold/review-item paths inherit the resolving
  evidence's marking via `forecasts.py::_ref_markings` threaded through
  `_reappend` and `record_transition`.

## R-02 — Semantic-change / watch value leak
- **Severity:** MAJOR
- **Invariant:** as R-01, on the semantic (pre-analytic) watch path.
- **Path:** `interpret_change` writes `prior_value`/`current_value`/`detail` from
  the changed observation, and `_propagate` writes claim-state + review items
  and `_raise_semantic_alerts` an alert — all at `ctx.marking`, while diffing a
  RESTRICTED manifestation.
- **Reproducer:** `tests/test_semantic_change_marking_leak.py` — CTX_B read the
  restricted value out of a PUBLIC `semantic_change` record.
- **Repair:** the change record inherits the diffed manifestations' markings;
  claim-state / review items / alerts inherit the change + claim markings
  (`curunir_semantic/changes.py`, `pipeline.py`).

## F-01 — Analytic-refresher whole-object marking downgrade  ⟶ CRITICAL
- **Severity:** CRITICAL (declassification of compartmented analyst-authored
  intelligence). Found by the Opus-5 round; the same defect class as R-01, in
  the sibling families the first repair did not touch.
- **Invariant:** a re-append never re-classifies DOWN.
- **Path:** every analytic `_reappend`/`_restate_path`/`_reappend_objective`
  (`themes`, `narratives`, `stakeholders`, `impact`) stamped `merged["marking"]
  = ctx.marking`. `propagate_semantic_changes` refreshes the whole analytic
  layer under one (PUBLIC) context, so refreshing a SPECIAL theme/narrative/
  assessment/objective/path there made its **current** version PUBLIC — its
  analyst title/description became readable by an uncleared actor.
- **Reproducer:** `tests/test_analytic_refresh_marking_leak.py` — a SPECIAL theme
  refreshed on the PUBLIC pass; asserted it stays SPECIAL and its title never
  reaches CTX_B.
- **Repair:** single chokepoint — `substrate.py::append_version` floors every new
  version on its prior version's marking; and each refresher additionally floors
  on own and raises for restricted basis via `reference_markings`.

## F-02 — Refresher transitions leak restricted-derived counts
- **Severity:** MAJOR
- **Invariant:** a transition's marking covers the state its detail summarizes.
- **Path:** refresher `record_transition` calls (theme SOURCE_DIVERSITY_CHANGED /
  WEAKENED / EVIDENCE_UPDATED, stakeholder BASIS_DEGRADED, impact EXPOSED /
  STALE) embedded degraded/contradiction/family counts over member claims while
  stamped `ctx.marking`, so a PUBLIC object resting on a SPECIAL claim revealed
  that claim's movement (existence/count) to an uncleared actor.
- **Repair:** basis claim markings (`substrate.claim_markings`) threaded as
  `reference_markings` into those transitions across all four refreshers.

## F-03 — Fail-closed marking join aborts the background batch
- **Severity:** MINOR / LIMITATION (availability, not disclosure — fail-closed is
  the safe direction)
- **Path:** `inherited_marking` (via `most_restrictive`) raised on an un-joinable
  pair (org-locked vs releasable, disjoint releasability) with no per-item guard
  in the watch/propagation loops, so one such manifestation could halt the whole
  pass.
- **Repair:** `inherited_marking` is now total on the derive path — see F-03b for
  the corrected mechanism.

## F-03b — the first F-03 repair (`_seal`) under-classified  ⟶ MAJOR (found by the second review round)
- **Severity:** MAJOR (write-down on the releasability/authority axis) — a
  repair-induced variant, dormant in the shipped single-authority PUBLIC-releasable
  fixtures but real for coalition/multi-authority missions.
- **Invariant:** a derived marking is viewable only by contexts that can view
  every input.
- **Path:** the first F-03 seal returned a *satisfiable* marking (org-locked,
  SUPERVISOR). Because `can_view` treats releasability and organisation as
  alternative axes, an org-locked seal is not a superset of a releasable input's
  viewers: a base-org SUPERVISOR without the partner release could view a record
  derived from partner-releasable state. Confirmed at the primitive and through
  the `append_version` floor for a same-authority coalition object.
- **Repair (two parts):** (1) `_seal` now returns a **deny-all** marking (reserved
  `curunir:unjoinable-seal` compartment, viewable by no one) — the only single
  Marking that never under-classifies when the axes are orthogonal; (2) the
  refresher `_reappend`s floor on the object's **own** marking and raise only by
  **material** references, dropping the spurious `ctx.marking` join that made a
  coalition object collide with the background context in the first place.
- **Regression lock:** `tests/test_marking_information_flow_property.py` — a
  property test asserting no write-down across every axis (compartment, role,
  releasability, organisation, authority), the coverage whose absence let this
  slip. NEW-2 (org-locked objects over-restricted to SUPERVISOR) is closed by the
  same `_reappend` floor fix.

## F-04 — Authoring / mutating a low-marked object over restricted evidence under-classifies ⟶ MAJOR (found by the third review round)
- **Severity:** MAJOR (disclosure of restricted analyst free-text). Pre-existing
  (not a regression from the campaign), but violates the campaign's own invariant
  and success criterion §107#4, so closed rather than deferred.
- **Invariant:** no active constructor materially derives from more restricted
  state and silently retains a weaker marking.
- **Path:** the authoring/mutator writes (`create_*`, `add_position`,
  `supersede_position`, `update_membership`, `add_variant`, assumption/response
  folds) threaded the object's own marking but not the material claim markings
  they embed. Confirmed: a PUBLIC assessment `add_position` citing a SPECIAL
  claim with free-text `"SEALEDINTEL …"` was readable by CTX_B.
- **Repair:** closed at the same single chokepoint — `append_version` now also
  raises the version to cover `material_claim_ids(record)` (per-family extraction
  of the claims an object rests on), so creation, mutation and refresh all
  inherit their material claims' markings regardless of the calling engine, not
  just the sites that remembered to pass `reference_markings`.
- **Regression lock:** `tests/test_analytic_authoring_marking_leak.py` — a
  call-site lock (authoring a theme over a SPECIAL claim; folding a SPECIAL claim
  into a PUBLIC theme), the coverage the primitive property test could not give.

## B — SSRF egress guard — SOUND (no defect)
- The Opus-5 round ran every enumerated bypass (decimal/octal/hex IP, IPv6
  zone/mapped/NAT64/6to4/compat, userinfo, trailing-dot, uppercase scheme,
  percent-encoded host) — all blocked — and confirmed all production traffic
  routes through `SAFE_DEFAULT_TRANSPORT`. Two bounded limitations (redirect GET
  fires before post-hoc refusal; DNS check-then-connect) are documented, not
  defects.

---

## Convergence

Four Opus-5 rounds (descriptive count). Round 4 verdict: **CONVERGED** — the
claim-axis under-classification is closed at a verified single chokepoint across
all 15 families; the deny-all seal closes the releasability/authority write-down;
F1/F2 remain closed; no repair-induced over-restriction. Round-4 backstop gap #1
(`analytic_forecast.proposition_refs` not extracted) is now **closed** — added to
`material_claim_ids`, with the forecast-resolution lock restructured to a
realistic PUBLIC-forecast-then-restricted-claim scenario that survives it.

**Open Critical/Major product defects:** none on any axis reachable via shipped
paths. Two documented, workbench-guarded, non-shipped-reachable LIMITATIONS
remain (see CURUNIR_V6_7_SECURITY.md): cross-analytic-object reference
inheritance, and access-relative projection of mixed-marking objects. The
identity gap (no cryptographic actor signing) is a documented, dependency-gated
deployment limitation, not a reachable product defect.

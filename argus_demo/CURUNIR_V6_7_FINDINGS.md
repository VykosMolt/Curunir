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

## Cryptographic identity (dedicated Opus-5 crypto review, §90)

The Ed25519 core, canonical serialization, replay re-verification, and session
hygiene held on first review; three MAJOR defects in the *enforcement seam*
between the signature and the act were found and repaired.

## F-05 — signed target not bound to the acted-on target ⟶ MAJOR
- **Invariant:** a signature binds exactly what is authorized; it cannot be
  transplanted across targets.
- **Path:** `verify_action` checked version/mission/actor/nonce but never
  compared the signed `target_id`/`action_type` to the operation performed, and
  the version token was a bare integer shared across reports — so a signature
  for report A verified and applied against report B at the same version.
- **Repair:** `verify_action` now takes and enforces `expected_action_type`/
  `expected_target_kind`/`expected_target_id`; the workbench version token is
  target-scoped (`workbench_report:{id}@v{n}`). Lock: WRONG_TARGET in
  test_identity_crypto.

## F-06 — signed action recorded before authorization ⟶ MAJOR
- **Invariant:** the signed-action log records acts that took effect, not
  refused ones.
- **Path:** the bridge recorded the SignedActionRecord inside `verify_action`,
  then ran the command; a four-eyes/validation refusal still left a GENUINE
  record and burned the nonce — fabricable non-repudiation evidence.
- **Repair:** split into verify (no record) → apply (authorization runs) →
  `commit_action` (record only on success). Lock: four-eyes refusal leaves no
  signed_action. **Bounded residual:** a crash between the command committing
  and the record append would leave an act unattributed (safe direction — never
  an unauthorized or fabricated act); documented as a crash-recovery limitation.

## F-07 — attacker-chosen signed timestamp ⟶ MAJOR
- **Invariant:** the signed "when" reflects reality and cannot be used to
  manipulate replay verdicts.
- **Path:** the timestamp came entirely from the client and was never bounded to
  server time, so an actor could backdate an act before a deadline or postdate
  it past a later key revocation (flipping a genuine act to non-genuine).
- **Repair:** `verify_action` rejects (CLOCK_SKEW) a timestamp deviating from
  server time by more than 300s, and the key-validity check uses that bounded
  value. Lock: CLOCK_SKEW in test_identity_crypto.

## F-08 — dual actor_kind source ⟶ MINOR
- The human-only gate used the workbench-registry `actor_kind` while the signed
  record used the key-registry kind; a misconfigured mismatch could pass the
  gate with a mis-attributed record. **Repair:** the bridge refuses
  (ACTOR_KIND_MISMATCH) when the two disagree. Lock in
  test_identity_signed_operations.

## Convergence

Four Opus-5 rounds (descriptive count). Round 4 verdict: **CONVERGED** — the
claim-axis under-classification is closed at a verified single chokepoint across
all 15 families; the deny-all seal closes the releasability/authority write-down;
F1/F2 remain closed; no repair-induced over-restriction. Round-4 backstop gap #1
(`analytic_forecast.proposition_refs` not extracted) is now **closed** — added to
`material_claim_ids`, with the forecast-resolution lock restructured to a
realistic PUBLIC-forecast-then-restricted-claim scenario that survives it.

The cryptographic identity substrate then had its own dedicated Opus-5 review
(two rounds): the Ed25519 core held; three MAJOR enforcement-seam defects
(F-05/06/07) and one MINOR (F-08) were found and repaired, and round 2 returned
**CONVERGED — no open Critical/Major**, with two bounded, non-attacker-triggerable
LIMITATIONS documented (F-06 crash window, F-07 ±300s timestamp).

**Open Critical/Major product defects:** none on any axis reachable via shipped
paths, across the marking, egress, provider, backup and identity work. Documented
bounded LIMITATIONS (see CURUNIR_V6_7_SECURITY.md): cross-analytic-object
reference inheritance and access-relative projection (both workbench-guarded,
non-shipped-reachable); the F-06/F-07 crypto residuals; and browser-side WebCrypto
signing (the server-side identity substrate is complete and enforces the model).

---

# V6.7 resilience continuation — adversarial review (two independent Opus-5 rounds)

The continuation tranche (browser identity last mile, resource governance, crash
recovery, migration, reconstruction, endurance) was reviewed by two independent
Opus-5 adversarial passes with running reproducers. Both found real defects; all
Critical/Major were repaired and locked, then the whole surface re-verified.

## Identity + resource-governance review (round A)

- **F1 — cross-actor signing-key hijack ⟶ CRITICAL.** `POST /api/auth/enroll`
  (and `KeyRegistry.enroll`) keyed a key family by the public-key digest but never
  checked ownership, so any bearer holder could retire/hijack another actor's
  signing key by resubmitting that actor's (non-secret) public key — permanent
  denial of the victim's signing authority + false identity-log entries. **Fix:**
  `enroll` refuses a public key already owned by a different actor and never
  resurrects a retired/revoked own key; the route surfaces it as 409. Locks:
  `test_identity_server::test_enroll_refuses_cross_actor_key_hijack`.
- **F2 — two-device self-brick ⟶ MAJOR.** The enroll route rotated on any new key,
  so two devices of one actor ping-ponged each other into a both-keys-RETIRED
  state (no adversary). **Fix:** a proper multi-device model — enroll ADDS an
  active key (never rotates/retires as a side effect) and `authenticate` verifies
  the challenge against ALL of the actor's active keys. Lock:
  `test_two_device_keys_for_one_actor_both_authenticate`.
- **F3/F6 — RSS DOCTYPE guard bypass / false-positive ⟶ MAJOR/MINOR.** The
  byte-regex missed a DOCTYPE in UTF-16 (measured 45× billion-laughs expansion)
  and false-flagged the token quoted in CDATA. **Fix:** detection via `expat`
  (encoding-agnostic, fires only on a real declaration, stops before expansion).
  Locks: `test_rss_utf16_billion_laughs_is_refused`,
  `test_rss_cdata_quoting_a_doctype_is_not_a_false_positive`.
- **F4 — truncation → semantic removal ⟶ MAJOR.** The §3 truncation exclusion
  landed in forecast absence but NOT in `changes.interpret_change`, so a
  byte-capped re-retrieval diffed as REMOVED_PROPOSITION / SOURCE_RETRACTION /
  VALUE_CHANGED and could move a claim to STALE/RETRACTED. **Fix:** under a
  truncated current manifestation every lifecycle-moving (loss/change) class is
  reclassified to UNRESOLVED_CHANGE; additions still flow. Lock:
  `test_truncated_re_retrieval_is_not_interpreted_as_removal_or_staleness`.
- **F5 — unbounded/unauthenticated challenge ⟶ MAJOR.** `_pending` grew without
  bound and `/api/auth/challenge`+`/time` were anonymous. **Fix:** prune expired +
  hard-cap `_pending`; bearer-gate the endpoints. Lock: `test_challenge_requires_a_bearer`.
- **F7 (canonical key sort by code point), F8 (executor infra faults propagate,
  not SOURCE_FAILED), F9 (pivot cap bounded in memory + visible)** — MINOR, all
  fixed and locked.
- NOT-A-DEFECT (reviewer-verified, live-probed): the GLEIF/EDGAR/Wayback/Wikidata
  reclassifications do not break genuine empties; the truncation exclusion at the
  acquisition layer is complete; the recorded_time re-stamp is sound; signature
  transplant/replay closed.

## Resilience / store-durability review (round B)

- **C-1 — content-triggered store corruption ⟶ CRITICAL.** The write path emits
  U+2028/U+2029/U+0085 raw (ensure_ascii=False) but the reader used
  `str.splitlines()`, splitting one committed event into two — any ingested text
  with a line/paragraph separator permanently bricked the store, and recovery
  mislabelled it as tampering. **Fix:** all readers + recovery split on `\n` ONLY
  (shared `_split_log`). Lock: `test_unicode_line_separators_in_text_do_not_corrupt_the_store`.
- **C-2 — recovery races a live writer ⟶ CRITICAL.** `recover_torn_tail` took no
  lock, so the advertised background self-heal could orphan a concurrent writer's
  committed bytes. **Fix:** recovery holds the append lock for the whole
  read-decide-install. Lock: `test_recovery_blocks_on_the_append_lock`.
- **C-3 — two-rename data-loss window ⟶ CRITICAL.** Install was move-then-rename
  with a window where `events.jsonl` was absent (a crash there → silent total
  loss, verify_chain VALID). **Fix:** COPY the crashed original aside, then ONE
  atomic rename. Lock: `test_recovery_preserves_original_by_copy_not_move`.
- **C-4/M-3 — four-eyes bypass on interrupted submit/approve ⟶ CRITICAL/MAJOR.**
  Submitters/approvers were derived only from the disposition (append #2); a crash
  losing it let the submitter self-approve. **Fix:** separation of duties also
  takes the event-envelope actors of the report's version/disposition events (the
  IN_REVIEW version is durable append #1). Lock:
  `test_four_eyes_survives_a_crash_that_loses_the_submitted_disposition`.
- **M-1/M-2 — unterminated / UTF-8-cut torn tail ⟶ MAJOR.** A committed-looking
  line with no trailing `\n`, or a tail cutting a multi-byte char, either merged
  into the next append or raised an uncaught UnicodeDecodeError. **Fix:** a
  non-`\n`-terminated trailing segment is a torn (uncommitted) write, detected and
  recovered; lines decoded per-line. Locks:
  `test_unterminated_final_line_is_treated_as_torn_and_recovered`,
  `test_torn_tail_cutting_utf8_sequence_recovers_without_decode_error`.
- **M-4 — version guard bypassed by the constructor ⟶ MAJOR.** `import_from`
  checked the contract version but a directory-copy/snapshot restore via the plain
  constructor did not. **Fix:** `__init__` refuses an incompatible contract
  version at every open. Lock: `test_constructor_refuses_incompatible_contract_version`.
- **M-5 — marking laundering ⟶ MAJOR.** `marking_from_record` injected
  `min_role=OBSERVER` for a missing value, turning a `can_view` deny into an allow
  on round-trip. **Fix:** fail closed to the most-restrictive role. Lock:
  `test_marking_reconstruction_does_not_launder_a_missing_min_role`.
- **M-6 — kernel identity under-specified ⟶ MAJOR.** The kernel hash covered 8 of
  ~31 transitively-loaded modules and the test never verified it. **Fix:** a
  whole-tree `argus_kernel_tree_sha256`, recomputed and asserted by
  `test_clean_reconstruction`.
- **m-1..m-4** (MINOR): reconstruction deps completed (httpx, pdftotext); endurance
  now verifies the ON-DISK chain, counts actual poison attempts, and tightens the
  fd bound; recovery preserves prior forensic remainders, cleans its temp file,
  and rolls back on any exception. All fixed.

## Continuation convergence

Two independent Opus-5 rounds found 4 + 4 = 8 Critical and 9 Major defects across
the continuation surface; every one is repaired with a behavioral regression lock,
and the full non-DB suite is green (only the two pre-existing, unrelated
capsule-stream failures remain). A confirmatory adversarial round over the repairs
follows.

## Confirmatory round over the repairs (round 2)

An independent confirmatory Opus-5 pass attacked the repairs. It confirmed 6 of 8
categories sound under running probes (store torn-detection/byte-math, the C-2
recovery lock, the M-4 version guard, F1 enroll + its race, F3/F6 RSS, F7
canonical) and found repair-induced or residual defects, all now fixed:

- **Four-eyes over-restriction (MAJOR)** — already fixed before the round landed
  (commit 4eabe46): the SoD set now takes only IN_REVIEW version-event actors +
  SUBMITTED-disposition actors, so a reviewer who returned a report for revision
  can still approve the revision.
- **C-3 rollback still non-atomic (MAJOR)** — the install was atomic but the
  ROLLBACK rewrote the live log with a plain `copyfile`, re-opening the silent-loss
  window. Fixed: validate the truncated content in memory (`_verify_kept`) BEFORE
  touching the file, so the swap always yields a clean store and no rollback
  exists; a partial/failed install cleans up and leaves events.jsonl untouched.
- **F8 aborted the plan on network faults (MAJOR)** — `except (OSError, …)` caught
  ConnectionReset/Timeout/SSL (all OSError subclasses = source faults). Narrowed to
  `MemoryError`/`StoreError` only; network/disk OSErrors stay contained as
  SOURCE_FAILED (one bad source never aborts the pass).
- **F4 only covered the transport cap (MAJOR)** — the normalizer's own field/region/
  pdf caps truncate with `manifestation.truncated == False`. `interpret_change` now
  keys on the document's `*TRUNCATED*` warnings too (`_current_read_truncated`).
- **F2 unbounded active keys ⟶ O(K) authenticate (MAJOR)** — added a per-actor
  active-key cap (`MAX_ACTIVE_KEYS_PER_ACTOR`); enrolment refuses beyond it.
- **F5 left `_sessions` unbounded (MINOR)** — sessions are now pruned + capped like
  `_pending`.
- **`rotate` self-brick (MINOR)** — enrolls the new key before retiring the old, so
  a failed rotate never bricks the actor.
- **Truncation suppressed additive source-corrections (MINOR)** — the reclassify
  now skips pure additions (`prior is None`), so a correction visible in the read
  prefix still flows.
- Stale enroll docstring corrected.

Round-2 repairs each carry a regression lock (durability C-3-refuse, executor
network-contained + MemoryError-propagates, identity active-key-cap, normalizer-
truncation, four-eyes reviewer-may-approve-revision). Full non-DB suite green
(only the two pre-existing capsule failures).

## Round 4 — convergence check found 2 MAJOR round-2 over-corrections (repaired)

The convergence check (attacking the round-2 repairs) confirmed 4 of 6 categories
sound (store recovery, four-eyes, F2/F5 only Minor) and found two MAJOR
over-corrections, now repaired:

- **F8 over-corrected (MAJOR)** — the type-based narrowing (`except MemoryError,
  StoreError`) sat over a `try` spanning two fault domains, so a LOCAL custody
  disk-full (OSError) was contained as SOURCE_FAILED, defaming every source. Fixed
  by scoping by SITE: only `connector.execute` is contained; the custody/store
  write is outside it, so a local disk fault propagates. Lock:
  `test_local_custody_disk_fault_propagates_not_source_failed`.
- **F4 over-broad (MAJOR)** — "any warning containing TRUNCATED" caught
  `REGIONS_TRUNCATED_AT_*`, an anchor-MAP cap where the content is COMPLETE, so a
  page with >400 blocks (or a >400-path feed) silently dropped a real
  ISSUED→REVOKED change — a false negative worse than the original. Fixed to match
  only content caps (`FIELDS_TRUNCATED*`, `PDF_TEXT_DERIVATIVE_TRUNCATED*`), with a
  robust manifestation-id document lookup. Lock:
  `test_region_map_cap_is_not_treated_as_content_truncation`.

Minor fixes from the same round: F5 evicts the flooding actor's OWN oldest session
(not a victim's); recover_torn_tail refuses a version-incompatible store up front
(truthful result); `_next_version` requires content_author with a content change.

Documented bounded LIMITATION (reviewer-rated MINOR, not a bypass): the per-actor
active-key cap is checked outside the append lock, so concurrent enrolments can
overshoot it by ~request-concurrency (one-shot: a second burst is fully refused,
and there is no HTTP revoke surface to reset it). The O(K) authenticate cost thus
returns only by a small bounded constant, never unboundedly.

## Round 5 — convergence check found 1 CRITICAL in the round-4 F8 fix (repaired)

The convergence recheck confirmed 4 of 5 categories sound (F4 content-cap match,
F5 eviction ordering, the constructor version guard, the content_author guard)
and found one CRITICAL the round-4 F8 site-scoping introduced:

- **F8 availability regression (CRITICAL)** — moving the manifestation build
  outside the containment also moved out where SOURCE-supplied values are first
  validated. A BENIGN source value (a date-only/naive GLEIF timestamp like
  "2026-01-01", or a lone-surrogate native id) then raised in the
  ManifestationRecord build, OUTSIDE containment, and PROPAGATED — aborting the
  pass and, worst of all, PERMANENTLY stopping the watch loop (the failing watch
  never advances its schedule, so it and every watch after it never run again).
  No attacker required. Contained throughout every prior revision; 4183091 was
  the first where it escaped.

  Root cause (across four F8 iterations): the fault domain was drawn by exception
  TYPE then by call SITE, when the real boundary is data PROVENANCE. Fixed
  accordingly: the manifestation build is back INSIDE the containment (so a
  source-derived value error → SOURCE_FAILED), and ONLY the local custody-disk
  write is tagged (`_LocalStorageFault`) so it alone propagates. Now:
  source/content/network faults → contained SOURCE_FAILED (plan continues); a
  local custody/OOM/store fault → propagates (stops the pass, right to). Locks:
  `test_source_supplied_bad_value_is_contained_not_aborting_the_plan`,
  `test_local_custody_disk_fault_propagates_not_source_failed`.

Documented LIMITATION (pre-existing, not this campaign): the extractor's
undeclared 200-line observation cap (`extract.py`) can read as a removal with no
warning to trigger the truncation guard; the warning-based guard structurally
cannot see extractor-level caps.

## Round 7 — narrow F8 recheck found the CRITICAL still reachable (repaired)

The round-6 provenance split guarded record CONSTRUCTION but not SERIALIZATION:
`finish()` (which appends the records, running the store's canonical encode — the
LAST validation a source value passes) sits outside the containment. So a
source-supplied lone surrogate in a MULTI-result response (>=2 hits → the
single-result digest check is skipped) reached the append and crashed it, still
propagating + permanently stalling the watch loop, and re-detonating in
`watch.py`'s own records. Round 8 fixes it at the true root — the acquisition
edge every source string crosses:

- **F8-E1 (CRITICAL)** — `scrub_surrogates` at the connector boundary
  (`NativeResult.__post_init__`, `ConnectorResponse.__post_init__`) replaces lone
  surrogates in EVERY source-supplied string with U+FFFD, so no such value can
  reach any record — closing the class in the executor, the watch loop, and every
  other consumer at once. `finish()`'s error_detail is scrubbed too (the
  containment handler builds it from a raised exception that may carry a source
  token). Locks: `test_source_strings_are_surrogate_scrubbed_at_the_connector_edge`,
  `test_multi_result_surrogate_response_is_serializable`,
  `test_multi_result_surrogate_does_not_abort_execution`.
- **F8-E2 (MAJOR)** — `finish()` appended the ExecutionRecord before its
  manifestations, so a StoreError between left a dangling manifestation_id.
  Reordered: manifestations first, then the referencing execution record (an
  unreferenced manifestation is benign; a dangling reference is not). Lock:
  `test_finish_appends_manifestations_before_the_execution_record`.
- **F8-E3 (MINOR)** — the custody catch was OSError-only; the content store's
  local-integrity ValueErrors (digest mismatch, chain invalid) fell through and
  defamed the source. Now caught as `_LocalStorageFault`. Lock:
  `test_custody_valueerror_propagates_as_local_fault`.

The round-6 CONSTRUCTION-side containment was confirmed correct (the date-only
timestamp + lone-surrogate single-result escapes are genuinely contained); this
round closed the SERIALIZATION side and the shared root (surrogate scrub).

## Round 9 — surrogate class CLOSED for executor+watch; MAJOR remained in the semantic plane (repaired)

The comprehensive recheck confirmed the executor + watch loop are CLOSED (the
permanent-stall escape re-run and confirmed dead; full connector×surrogate sweep
clean) and found the class still open where the connector scrub cannot reach —
the response BODY, re-derived into strings by the semantic normalizer:

- **F8-A (MAJOR)** — a lone surrogate in the body survives `json.loads` (a
  \udXXX escape) or a utf-7/declared-charset HTML decode, then crashes the
  normalizer's own `json.dumps(...).encode()` / `put_payload(text.encode())`.
  Contained as PROCESSING_FAILED (no corruption, honest OPEN item) but the
  document is permanently unprocessable — invisible to the world model — after
  auto-retry exhausts. Fixed: `normalize._scrub_surrogates` on the derived text /
  title / publisher and `errors="replace"` on the fields payloads (the
  semantic-plane analogue of the connector scrub; consistent with the xml/text/
  pdf branches). Lock: `test_json_body_with_lone_surrogate_normalizes_and_is_not_lost`.
- **F8-B (MINOR, regression this campaign introduced)** — `scrub_surrogates`
  assumed str; a non-string scalar in an identifier slot raised AttributeError
  → discarded the record. Now coerces via `str()`.
- **F8-C (MINOR, pre-existing)** — a surrogate in `request.value` crashed
  `urllib.quote` before the NativeResult edge. Now scrubbed at `ConnectorRequest`.
- **F8-D (MINOR, residual in the E1 repair)** — `finish()` scrubbed error_detail
  for the ExecutionRecord but not for `record_source_status`. Now scrubbed once at
  the top of `finish()`. Lock: `test_scrub_handles_non_string_and_request_value`.

Confirmed NOT-A-DEFECT: the E2 reorder (net improvement — an under-claim, the
doctrine-safe direction), non-surrogate serialization hazards (NaN/Inf/bigint/
bytes — no reachable source→record path), and E3 (custody ValueErrors are local).

## Rounds 10–12 — surrogate class swept to the remaining external boundaries + two non-surrogate durability gaps (repaired)

Round 10 re-confirmed the executor / watch / semantic closures and the
non-surrogate durability suite (crash recovery, marking, four-eyes, identity)
as CONVERGED. Round 11 then swept the surrogate class across the *remaining*
external boundaries — the analytic provider and the workbench HTTP render — and
audited two adjacent durability surfaces (import install, browser signing). Five
findings; all repaired in round 12 with regression locks:

- **F-P1 (MAJOR)** — the analytic provider's `infer_fn` is a pluggable EXTERNAL
  boundary exactly like a connector, but its returned dict was written into a
  proposal record un-scrubbed; a lone surrogate in a provider-returned string
  crashes `canonical_bytes` when the record is appended (the whole proposal is
  lost, not contained). Fixed: `providers._scrub_output` recursively scrubs
  `dict(self.infer_fn(task, inputs))` inside the propose() try, matching the
  connector-edge doctrine. Lock:
  `test_provider_surrogate_output_does_not_crash_and_is_recorded`.
- **F-W1 (MAJOR)** — the workbench translates a command failure into an HTTP
  error whose detail Starlette UTF-8-encodes; a lone surrogate in that detail (or
  in an echoed field) 500s the response *render* instead of returning the honest
  4xx. A well-formed JSON body cannot carry a lone surrogate (the client's UTF-8
  body encode refuses it — proven by `test_json_body_transport_cannot_carry_a_
  lone_surrogate`), so this is defense-in-depth for a surrogate arriving from
  STORED data or a non-body channel. Fixed: a shared `server.render_safe` helper
  applied to `_detail(error)` and the challenge actor_id echo. Lock:
  `test_render_safe_neutralizes_a_surrogate_reaching_the_response`.
- **F-I1 (MAJOR)** — a hostile/corrupt export that passes the manifest hash + the
  contract-version gate but fails the FINAL `cls(new_root)` open (e.g. a broken
  hash chain re-hashed into the manifest) left an un-openable half-installed
  store in the caller's target root and raised an untyped error. Fixed:
  `import_from` wraps the post-install open in try → remove the target root →
  `StoreError`; `recover_torn_tail`'s initial-open catch broadened to `Exception`
  so a non-OSError open failure there is contained too. Lock:
  `test_failed_import_rolls_back_and_leaves_no_debris`.
- **F-B1 (MINOR)** — `watch.py` appended the `WatchRun` summary before the change
  records it references; a crash in that window left a `WatchRun` pointing at
  changes that were never written (the same dangling-reference hazard as F8-E2).
  Reordered: change records first, then the referencing `WatchRun`.
- **F-J1 (MINOR)** — the browser canonical serializer (`canonical.js`) accepted a
  lone surrogate that the Python verifier's `canonical_line` RAISES on, so the
  browser would sign a payload the server cannot reproduce — a silent signature
  DIVERGENCE (every such signature fails verification), not a crash. Fixed:
  `canonical.js` refuses a lone surrogate (`isWellFormed`, regex fallback),
  matching the existing float / unsafe-integer refusal doctrine. Lock: the
  lone-surrogate cases added to `REFUSED_CORPUS` in the JS↔Python parity proof.

The surrogate class is now closed at every external→record boundary reached by
review: connector edge, connector request, executor manifestation, watch loop,
semantic normalizer, analytic provider, workbench render, and the browser
signer. F-I1 and F-B1 extend the crash/dangling-reference doctrine (validate
before swap; referent before referrer) to the import-install and watch-summary
surfaces.

## Round 13 — independent audit found 4 MAJOR (repaired round 14), and CORRECTED a false premise

Round 13 was the merge-decision convergence round. It returned `NOT_CONVERGED`:
the independent reviewer reproduced four MAJOR defects with live probes and
found bounded MINOR residuals on four round-12 fixes. All repaired in round 14
with regression locks. Crucially it overturned a premise this campaign had
recorded as fact — that the wire cannot carry a lone surrogate — so the honesty
correction is documented here alongside the fix.

- **NEW-1 (MAJOR)** — an unauthenticated **500 on every POST endpoint** via a
  JSON `\uD800` escape. The F-W1 premise ("a well-formed JSON body cannot carry a
  lone surrogate") was **FALSE**: JSON `"\uD800"` is pure ASCII on the wire, a
  browser's well-formed `JSON.stringify` emits exactly that, `json.loads`
  restores the lone surrogate, and it reaches Pydantic body validation BEFORE the
  endpoint / before auth. FastAPI's default `RequestValidationError` handler
  echoes the offending input verbatim, and Starlette's UTF-8 render 500s on it.
  Fixed: an app-level `@app.exception_handler(RequestValidationError)` that deep-
  scrubs the echoed errors before render. The false premise was corrected in the
  `render_safe` docstring, this document, and the (renamed, rewritten) lock.
  Lock: `test_ascii_escaped_surrogate_body_yields_422_not_500`.
- **NEW-4 (MAJOR)** — an unauthenticated `TypeError` → 500 on the WHOLE API from
  one non-ASCII byte in the `Authorization` header. Starlette decodes header
  bytes as latin-1, so the bearer token can carry a byte ≥ 0x80;
  `secrets.compare_digest` RAISES on a non-ASCII `str`, escaping `context()`.
  Fixed: `auth.context_for` rejects a non-ASCII token as an ordinary `AuthError`
  (→ 401) before the compare. Locks:
  `test_non_ascii_bearer_token_fails_closed_not_typeerror` (fix site) and
  `test_non_ascii_bearer_over_http_is_401_not_500` (raw-ASGI end to end).
- **NEW-2 (MAJOR)** — `import_from`'s install phase ran OUTSIDE the rollback try,
  so a mid-install failure (a payload whose content fails its hash, a payload
  file absent, a mid-copy `OSError`) left a store that OPENS and reports
  `verify_chain: valid` while silently missing payload content — and a target
  root that then refuses a retry — sometimes raising an untyped `FileNotFoundError`.
  Fixed: the ENTIRE install (from the first `mkdir`) is inside the try; on any
  failure the whole freshly-created root is removed (or just our artifacts if the
  root pre-existed) and a typed `StoreError` is raised; `KeyboardInterrupt` /
  `SystemExit` are re-raised, never suppressed (this also closes the F-I1
  MINOR). Lock: `test_import_install_failure_rolls_back_whole_root_and_is_typed`.
- **NEW-3 (MAJOR)** — the C-1 `str.splitlines()` class was still open in
  `delta.py` (the privileged store-to-store sync / disaster-recovery import).
  `build_delta_bundle` writes `canonical_line(e) + "\n"` under `ensure_ascii=
  False`, so a U+2028/U+2029/U+0085 in ordinary text (routine in scraped/pasted
  web content) went to the file raw and `str.splitlines()` tore the event in half
  — `verify_delta_bundle` reports valid, then import raises `JSONDecodeError`. So
  any mission whose text ever held a Unicode line separator could never be
  replicated or restored via the delta path. Fixed: split on `\n` only. Lock:
  `test_delta_import_splits_on_newline_only_not_unicode_separators`.

MINOR residuals from round 12, all repaired round 14:

- **F-P1 residual** — `_scrub_output` handled only `str`/`dict`/`list`/`tuple`
  (a **set** value, which canonical DOES serialize, slipped through), and the
  caller-supplied `task`/`inputs` were hashed for `inference_id`/`input_hash`
  OUTSIDE the try. Fixed: `_scrub_output` covers `set`/`frozenset`; `task`/
  `inputs` are scrubbed up front. Lock:
  `test_provider_scrub_covers_sets_and_caller_task_inputs`.
- **F-J1 residual** — object KEYS bypassed the guard (`JSON.stringify(k)` instead
  of `canonical(k)`), so the browser would still sign `{"\uD800":1}` that the
  Python verifier RAISES on. Fixed: keys route through `canonical()`. Lock: key
  cases added to `REFUSED_CORPUS`.
- **F-B1 residual** — `WatchRun` and `ChangeObservation` are MUTUALLY
  referential, so no append order satisfies both; the change→run `run_id` was
  consumed as an alert evidence fallback (`mission_bridge.py`) and could dangle
  after a torn tail. Fixed: the alert falls back to the change observation's OWN
  id (which necessarily exists), never the run_id. The residual torn-tail
  double-OBSERVATION (a benign duplication in the crash window, not loss or
  corruption — the safer direction) is a registered bounded limitation; a fully
  idempotent watch retry (content-derived ids) is out-of-scope future work. Lock:
  `test_retrieval_failure_alert_cites_change_id_not_a_possibly_dangling_run`.

Additional (reviewer-noted, uncounted) hardening: `KeyRegistry._transition` /
`revoke` stamped the registry's ambient marking on a status re-append rather than
flooring on the key's PRIOR marking — a no-write-down hole, unreachable while
every registry is PUBLIC but latent for a compartmented deployment. Closed by
`inherited_marking(self._marking_for(), [prior])`.

Verified sound by the same audit (no finding): the signed-action path (a
surrogate yields an honest 401, never a 500), store append atomicity (a refused
record leaves zero bytes), query/path/header surrogate delivery (all decode to
U+FFFD or latin-1), the connector edge and semantic normalizer, executor fault
domains, four-eyes/SoD, `export_html` escaping, fail-closed defaults, and
export/manifest hashing.

## Round 15 — reconvergence found the surrogate class's TWIN (non-finite floats) + 2 more render vectors (3 MAJOR, repaired round 16)

Round 15 independently re-verified the round-14 repairs — NEW-4, NEW-2, NEW-3,
F-P1, F-J1, F-B1 and the KeyRegistry floor all CONFIRMED_CORRECT with live
probes — but returned `NOT_CONVERGED`: NEW-1 was not fully closed (two more ways
the validation-error echo 500s), and it found the class the whole campaign had
been circling without naming — **a value that canonical serialization ACCEPTS
but that is not valid interchange JSON**. Three MAJOR, all repaired round 16.

- **NEW-A (MAJOR)** — unauthenticated 500 on every POST via a **non-UTF-8 body**.
  The round-14 handler ran `jsonable_encoder(exc.errors())`, and FastAPI's
  `ENCODERS_BY_TYPE[bytes]` does `o.decode()`, which raises `UnicodeDecodeError`
  on a non-UTF-8 `input` BEFORE the scrub. Fixed: `_deep_render_safe` is now
  TOTAL — it handles `bytes` (decode replace), and the handler no longer calls
  `jsonable_encoder`. Lock: `test_non_utf8_and_nan_bodies_yield_422_not_500`.
- **NEW-B (MAJOR)** — unauthenticated 500 on every POST via a **NaN/Infinity
  float** in a JSON body. `json.loads` accepts them; Starlette renders with
  `allow_nan=False`; the pass-through fallthrough let them reach the render.
  Fixed: `_deep_render_safe` maps a non-finite float to `null`. Same lock.
- **NEW-C (MAJOR)** — the twin, at the RECORD boundary. The kernel
  `canonical_bytes` uses `json.dumps` at default `allow_nan=True`, so a NaN/Inf
  serialized to a bare non-RFC-8259 token. An ordinary ANALYST POSTing
  `/api/commands/saved-views` with `{"definition":{"zoom":NaN}}` committed the
  event, THEN 500ed the response — permanently 500ing the record projection that
  replays it (append-only, no recovery), replicating the poison through
  export/delta bundles, and (via a signed `payload`) writing a non-reproducible
  `signed_payload` into a non-repudiation record while `replay` still reported
  GENUINE — all with `verify_chain` still valid. Fixed at the single
  serialization seam: `canonical_line` now REFUSES any non-finite float
  (`reject_non_finite`), so **no record can carry one**. This closes both HTTP
  sinks automatically: `save_view`'s append raises `ValueError` → honest 400
  (nothing committed); the signed path's `verify()` re-canonicalizes the payload,
  the raise is caught as a bad signature → 401. Locks:
  `test_canonical_line_refuses_non_finite_float`,
  `test_non_finite_float_saved_view_is_refused_not_committed`,
  `test_signed_payload_with_non_finite_is_refused_not_recorded`.

Confirming the source→record path was already closed for this class (the
connector edge `str()`-coerces and the normalizer stringifies), the exposure was
the workbench's free-form dict fields; the seam fix covers those and everything
else uniformly.

MINOR / hardening repaired the same round:

- **M6** — `KeyRegistry.rotate`'s "rotated-in" re-append stamped the ambient
  marking; now floors on the superseded key's marking (completes the round-14
  `_transition`/`revoke` no-write-down fix).
- **M1** — `import_from` into a non-directory target raised an untyped
  `NotADirectoryError`; now a typed `StoreError` up front.
- **M4** — `import_from` read a payload file named by the untrusted manifest
  BEFORE the hash check (an arbitrary-file-read / existence-oracle via a hostile
  `../../..` name); now the name must be a 64-hex content digest first.
- **M5** — two authenticated 500s where a 4xx is due: the forecast
  `proposition_refs` model is now `list[tuple[str,str]]` (bad shape → 422), and a
  malformed `resolution` dict → `ValueError` → 400 (not an uncaught `TypeError`).
- **M8** — `canonical.js` refused `-0` (JS "0" vs Python "-0.0"); parity corpus
  updated.
- **M9** — a hostile delta bundle with invalid UTF-8 → typed `StoreError`.
- **M11** — a malformed actor registry mid-edit → `AuthError` (401), not an
  untyped 500 on every request.
- **ValueError→Conflict consistency** — `save_view` / `resolve_review_item` /
  `create_hypothesis` relabelled a malformed-content `ValueError` as 409; now
  only a store `StoreError` (a real version conflict) is a 409, content errors
  stay 400 (the forecast path already did this).

Registered bounded (reviewer-assessed, not merge-blocking): M2/M3 (importing over
an existing non-store directory overwrites/leaves debris — inherent to
import-over-existing), M7 (a source `Infinity` lands in a text-only content
payload, never re-parsed), M10 (legacy `str.splitlines()` readers in
`partition_custody`/`v3..v5_3`/`scenario`, proven unreachable from the V6.7
product by an import-graph probe), and the F-B1 torn-tail double-observation
(over-reporting, the safe direction).

## Round 17 — reconvergence found the UNSWEPT HALVES of two round-16 fixes (2 MAJOR, repaired round 18)

Round 17 confirmed every round-16 repair CORRECT at the seam it targeted, but
returned `NOT_CONVERGED`: both round-16 MAJORs had a second reachable boundary
the fix did not cover. All repaired round 18; a subsequent SELF-REVIEW (before
dispatching round 19) found and closed two further unswept halves.

- **MAJOR-1** — `import_from` installs the export's `events.jsonl` (and
  `store_meta.json`) VERBATIM as bytes, so NEW-C's `canonical_line` seam never
  runs on an imported log. A hostile export whose manifest hashes match — or a
  backup written by pre-NEW-C code, or a perfectly legal `1e400` that `json.loads`
  returns as `inf` — reconstructs a poisoned, chain-VALID store the record
  projection 500s on forever, re-propagated by every later export and with delta
  sync permanently broken. Fixed: `import_from` now validates every event AND
  `store_meta` BY VALUE (`reject_non_finite`, which also catches `1e400`→`inf`)
  before install, refusing a poisoned backup rather than silently reconstructing
  it. (The delta path was already correct — `append_imported_event`
  re-serializes through `canonical_line`.) Locks:
  `test_import_refuses_non_finite_event_log`,
  `test_import_refuses_non_finite_store_meta`.
- **MAJOR-2** — the non-finite twin was open at the analytic provider:
  `_scrub_output` handled surrogates and containers but passed non-finite floats
  through, and the `INFERENCE_RECORDED` append sits outside containment, so a
  non-finite from the external `infer_fn` crashed the append and LOST the
  invocation's non-repudiation record (the non-finite half of F-P1). Fixed:
  `_scrub_output` maps a non-finite float to `null`. Lock:
  `test_provider_non_finite_output_is_scrubbed_and_recorded`.

MINOR repaired the same round:

- **MINOR-1** — the `except ValueError → Conflict` narrowing was incomplete:
  `annotations.py` and `reports.py:_next_version` still relabelled a
  malformed-content `ValueError` as a 409. Now both catch `StoreError` only, so a
  content error stays 400 (only a real version conflict is 409). Lock:
  `test_report_edit_with_surrogate_is_400_not_409`.
- **MINOR-2** — report `sections` is a free-form `list[dict[str, Any]]`, so a
  wrong scalar type (a numeric title, a non-list `sentences`, a string
  `basis_refs`) reached a `.strip()`/`.replace()`/`list()` and 500ed for an
  authenticated ANALYST across many field positions. Fixed: a
  `_validate_section_shapes` structural guard at the command boundary (400, not
  500). Lock: `test_malformed_report_section_is_400_not_500`.
- **MINOR-3** — delta import raised an untyped `JSONDecodeError`/`ValueError` and
  could apply a bundle PARTIALLY before a non-finite event aborted it mid-loop.
  Fixed: typed `StoreError` on a malformed line, and every event is
  non-finite-validated BEFORE any is applied (atomic). Lock:
  `test_delta_import_refuses_non_finite_atomically`.

Self-review (round 18, before dispatching the independent round) found two more
unswept halves of the same wrong-type/twin class and closed them:

- the SIGNED `payload`'s command extraction 500ed for a valid key-holder on a
  non-dict `command` or a non-list `acknowledge_dissent` (the apply lambda's
  `.get()`/`tuple()`); now a 400. Lock:
  `test_signed_command_wrong_shape_is_400_not_500`.
- `_validate_section_shapes` initially missed `sentence_id`/`section_id` (the
  12th/13th 500 positions); a fuzz of all field positions confirmed zero 500s
  after adding them. Lock: `test_report_sentence_id_wrong_type_is_400_not_500`.

Registered bounded (unchanged / reviewer-assessed): a legacy IN-PLACE store
already carrying a non-finite (written by pre-fix code) still opens — only the
IMPORT of a foreign/hostile backup is guarded, deliberately, to avoid bricking an
operator's own store on open; a ~1200-deep nested request body still hits
Python's recursion limit in the kernel `canonical_bytes` (pre-existing, framework
depth, authenticated); `import_from` follows a symlink-to-directory target
(operator-supplied).

## Round 19 — reconvergence found the SET/totality half of the provider fix (1 MAJOR, repaired round 20)

Round 19 CONFIRMED_CORRECT the whole of MAJOR-1 (import by-value guard — probed
with correctly re-hashed hostile exports: NaN/Infinity/1e400/deep/envelope/meta
all refused, clean backups still import), MINOR-1, MINOR-2 (a raw-bytes fuzz of
every report/save-view/forecast/signed field × hostile tokens = 0 authenticated
500s), the id-field and signed-command guards, and re-verified four-eyes,
fail-closed disabled-actor, and connector-edge coercion. It returned
`NOT_CONVERGED` on one MAJOR plus MINORs — again unswept halves:

- **N-1 (MAJOR)** — `_scrub_output` was not TOTAL: a non-finite inside a `set`
  became `{None, 1.0}`, which the kernel's set-SORT then crashed on
  (`'<' not supported between float and NoneType`); `bytes`, `Decimal`, and
  mixed-type dict keys (also sorted) escaped the `return value` fallthrough — each
  crashing the InferenceRecord append (built OUTSIDE containment) and losing the
  non-repudiation record, the exact MAJOR-2 harm. Fixed: `_scrub_output` is now
  total — a set becomes a deterministic list (members scrubbed, non-finite
  dropped, ordered by canonical string form so it never depends on cross-type
  sorting), non-string dict keys become their string form, bytes decode, and any
  other object becomes its scrubbed `str()` (mirroring `_deep_render_safe`). Lock:
  `test_provider_scrub_is_total_no_container_escapes_containment`.
- **N-2 (MINOR)** — a non-str `timestamp` in a signed payload hit
  `parse_time(...).replace` → `AttributeError`, uncaught → authenticated 500. Now
  caught → 401 unparseable. Lock: `test_signed_timestamp_non_string_is_401_not_500`.
- **N-3 (MINOR)** — a JSON-valid but structurally-malformed delta event (scalar
  line, missing seq, scalar record) applied PARTIALLY then crashed untyped. Now a
  shape check (dict envelope, int seq, str entry_hash, dict record) refuses it
  atomically before any apply. Lock:
  `test_delta_import_refuses_malformed_envelope_atomically`.
- **N-4 (MINOR)** — a by-VALUE check cannot catch a duplicate-key token
  (`"score":NaN,"score":1.0` parses finite but leaves a bare NaN in the archived
  bytes); import now parses strictly (`object_pairs_hook` refusing duplicate
  keys). Lock: `test_import_refuses_duplicate_keyed_and_deeply_nested_events`.
- **N-5 (MINOR)** — a deeply nested hostile export raised an untyped
  `RecursionError` from the import pre-pass (events AND store_meta); both now
  typed `StoreError`. Locks: the events case above +
  `test_import_refuses_non_finite_store_meta` (deep-meta case).

Self-review (round 20, before the independent round) again caught two more unswept
halves of its OWN fixes and closed them: the store_meta parse initially caught
only `ValueError` (not `RecursionError`), and the delta shape check initially
checked `"record" in event` but not that `record` is a dict.

Accepted (reviewer-assessed hygiene, NOT touched to avoid regression risk): N-6a
`move_forecast` still routes 409-vs-400 by `"version" in str(error)` rather than a
type test — confirmed non-misfiring (its version conflict is a StoreError; its own
validation ValueErrors never carry the word "version"); N-6b `build_delta_bundle`
on a LEGACY store already carrying a non-finite raises a bare ValueError from
canonical_line — such a store can be exported but its backup can never be
restored (a fail-closed operational consequence). Also bounded: a legacy IN-PLACE
store carrying a non-finite still opens (only the import of a foreign backup is
guarded); a duplicate-key export is now refused, but the by-value guarantee
remains value-level not byte-level for any residual construction the strict parse
does not reach.

## Round 21 — TWO concurrent Opus 5 reviewers (poison-value completeness + everything else); repaired round 22

To converge faster, round 21 ran two independent Opus 5 reviewers in parallel:
Reviewer A owned the poison-value class, Reviewer B owned auth/four-eyes/crypto/
crash-consistency/markings/resource/egress + the merge view. Both `NOT_CONVERGED`;
all findings repaired round 22. B's enforcement-core assessment was strong ("could
not break it" across auth, four-eyes on both paths and every crash-truncation
point, the Ed25519 order, torn-tail at every byte offset, write-side floors, 1134
hostile GETs → 0 5xx). The findings were, tellingly, the SAME "unswept-half"
pattern the campaign had been closing for serialization, now in the durability /
resource / gate classes — an accepted repair not swept to a sibling site.

Reviewer A (poison-value):
- **N-1 (MAJOR)** — `_scrub_output` was total for 43/48 exotic types but a Python
  `int` > `sys.get_int_max_str_digits()` (4300 digits) passed through and crashed
  the out-of-containment `sha256`, losing the audit. Fixed: the int branch
  `try: str(value)` and maps a too-large int to null. Lock: added to
  `test_provider_scrub_is_total_no_container_escapes_containment`.
- **NEW-A1/A2/N-3 (MINOR)** — the `export_manifest.json` parse (import) and the
  `delta_manifest.json` parse + delta envelope shape were unguarded/incomplete.
  Fixed: manifest parses are typed + `isinstance(dict)` + required-keys (import) /
  fail-closed (verify_delta_bundle); the delta envelope check now covers
  `event_type`/`recorded_time`/`actor`/`prev_hash`. Locks:
  `test_verify_delta_bundle_fails_closed_on_malformed_manifest`.
- **NEW-A3 / B-4 (MAJOR — both reviewers)** — the delta path read+persisted a
  payload named by an untrusted `payload_ref` before verifying (the `import_from`
  M4 arbitrary-file-read, unswept on the delta path). Fixed: `payload_ref` must be
  a 64-hex digest before use as a path (`_is_digest`), in both
  `import_delta_bundle` and `verify_delta_bundle`. Lock:
  `test_delta_import_refuses_non_digest_payload_ref_traversal`.
- **NEW-A4 (MINOR, out-of-tranche)** — the V2 `connectors.py` idempotency hash of
  a source `event_id` was outside the parse try (surrogate → record lost). Fixed:
  scrub a str `event_id`.

Reviewer B (everything else) — three no-attacker, shipped-path MAJORs:
- **B-1 (MAJOR)** — a PUBLIC strategic warning RE-projected over a forecast raised
  to SPECIAL after the warning was first raised under-classified the restricted
  forecast's state (resolution/probability readable by an uncleared actor), and
  `CURUNIR_V6_7_SECURITY.md` documented this as "not reachable" — false, re-
  projection is exactly where it lands. Fixed: `project_warning` floors the
  warning's marking on the forecast's AND objective's LIVE marking at both the
  RAISED projection and every re-projection; the doc's reachability claim is
  corrected. Lock: `test_warning_floors_marking_on_referenced_forecast`.
- **B-2 (MAJOR)** — `export_to` took no append lock and no catch-up, so a stale
  multi-writer instance wrote a manifest head disagreeing with the copied
  events.jsonl → a backup `import_from` then silently refuses (the documented
  rollback mechanism, invalid with no signal at backup time). Fixed: hold the
  append lock + `_catch_up()` across the copy + manifest (the recover_torn_tail
  doctrine applied to backups). Lock:
  `test_export_is_locked_and_caught_up_so_backups_are_restorable`.
- **B-3 (MAJOR)** — a NEW class: an access-filtered GATE (not just a view) can
  fail open. `open_dissent` read the APPROVER's filtered projection, so a
  compartmented dissent invisible to the approver → plain APPROVED (asserting "no
  dissent" over a standing dissent). Fixed: `approve_report` also reads dissent
  from the RAW store and fails CLOSED on any open dissent the approver cannot see
  (the dissent analogue of UNRESOLVABLE_BASIS). Lock:
  `test_compartmented_dissent_invisible_to_approver_blocks`.
- **B-5 (MINOR)** — `_prune_pending` evicted the globally-oldest (a victim's)
  challenge, not the flooder's own (the round-4 `_prune_sessions` fix, unswept).
  Fixed: own-actor eviction. Lock:
  `test_pending_challenge_flood_evicts_flooders_own_not_the_victim`.
- **B-6 (MINOR, unwired)** — the provider egress gate classifies the DECLARED
  `input_refs`, not the `inputs` cargo. Doc corrected; to be bound when a provider
  is wired.

New classes B named: (1) access-filtered GATES can fail open (B-3); (2) doctrine
established for one artifact not swept to its siblings (B-2/B-4/B-5) — the same
unswept-half pattern, now beyond serialization.

## Round 23 — ROOT-CAUSE PASS: the campaign stopped enumerating instances and closed the classes

Two concurrent Opus 5 reviewers (A = serialization/delta/import/export; B = markings/gates/auth) both returned `NOT_CONVERGED` and, independently, both diagnosed the SAME thing the operator did: 22 rounds had been killing instances, not causes. Reviewer B even did the mechanical sweep ("39 of 62 record_transition sites pass no marking") and named the root: *"the transition/derived-record marking chokepoint does not exist."* One of round-22's own fixes had already sprouted an unswept half (A-F3: verify_delta_bundle's new early-returns lacked the `failures` key its consumer reads). The operator chose to fix the CLASSES. This round adds the four missing chokepoints/rules and tests them at CLASS level, not instance level.

**Chokepoint 1 — the derived-record marking floor (closes R23B-1 CRITICAL, R23B-2, R23B-4).** `substrate.record_transition` now floors every transition on the SUBJECT object's own current marking (via a shared `_subject_marking`), the append_version no-write-down guarantee extended to transitions — so no engine call site can forget it. Engine-written review items floor through the sibling `marked_for_subject`. This closes the whole class at once: a warning's transitions/alerts, a degraded-basis transition, and a compartmented indicator's analyst text can no longer under-classify. Lock: `test_no_derived_record_underclassifies_its_restricted_subject` — a PROPERTY over every transition + review_item in the store after a background pass, not a single instance.

**Chokepoint 2 — a control that BLOCKS reads the RAW store (closes R23B-3 MAJOR).** `approve_report` now runs `_raw_hidden_basis_concerns` (the `_raw_open_dissent` shape): a cited basis whose current claim-STATE (retraction) or OPEN review the approver cannot see fails the approval CLOSED, rather than validate_report silently counting the invisible concern absent and approving retracted evidence as settled fact. Lock: `test_hidden_claim_state_on_cited_basis_blocks_approval`.

**Chokepoint 3 — shared/atomic durability primitives (closes A-F1 & A-F2 MAJOR + A-F3/4/5/6 MINOR).**
- `_referenced_payloads` detects payloads FAMILY-AGNOSTICALLY (any record field naming an existing payload), so the delta/full bundle carries a V6.7 mission's `semantic_document` evidence instead of shipping zero payloads while declaring "none omitted"; import installs ALL manifest payloads. Locks: `test_delta_referenced_payloads_is_family_agnostic`, the round-22 delta suite.
- `put_payload` is now crash-atomic (temp + fsync + rename) and SELF-REPAIRING (an existing file that does not hash to its name is a torn write → rewritten); `get_payload` fails LOUD on a corrupt payload. Closes the "one interrupted write silently breaks every later backup + serves wrong evidence" durability hole (the B-2 harm, swept to payloads). Lock: `test_put_payload_repairs_a_torn_write_and_get_payload_fails_loud`.
- `verify_delta_bundle`/`import_delta_bundle`/`import_from` manifest handling: base_entry_hash required, IsADirectoryError typed, a non-list `payloads` is a failure not a skip, and the consumer reads `failures`-or-`reason` (A-F3/4/5/6).

**Chokepoint 4 — two mechanical rules (closes R23B-5, R23B-6 MINOR).** `/api/auth/challenge` accounts the pending-map cap-eviction to the BEARER principal (`owner`), not the client-chosen `actor_id`, so a flooder cannot evict other actors' live challenges. The projection id-scrub covers every truncation length the code actually persists (`[:20]`, `[:12]`), derived from the emitted cuts.

Reviewer B's enforcement-core assessment was, again, that it could not be broken (auth, four-eyes on both paths, Ed25519 order, key no-write-down, marking write-side floors, torn-tail recovery). This pass adds the MISSING chokepoints; it does not reopen the working ones. Broad sweep 5090 passed / 0 regressions.

## Round 24 — CONFIRMATORY ATTACK on the root-cause diff broke it; repaired

The operator refused to merge on `9d0760d` and applied the convergence rule correctly: the root-cause diff was the ONE load-bearing change never independently attacked; class-level property tests prove the invariant over encoded cases, not that the chokepoint cannot be bypassed or composed wrong. Two fresh Opus 5 reviewers attacked `9d0760d` (A = marking/gate, B = durability). **BOTH returned DIFF_BROKEN.** The diagnosis reproduced ITSELF inside the commit: three of four chokepoints were still per-site, and the property tests asserted the invariant the code already enforced.

- **A1 (CRITICAL)** — `record_transition` floored on the SUBJECT, but a transition's detail routinely quotes a NON-subject object. `indicators.py` `INDICATOR_FIRED` writes a SPECIAL indicator's description into a PUBLIC transition; the round-23 fix was applied to the review item seven lines above and skipped the transition. A2/A3 (MAJOR): `PROBABILITY_UPDATED`, and `forecasts.py` `EXPECTED_NOT_OBSERVED` floored on the claim not its subject forecast. A4 (MINOR): `_fold`/`impact`.
- **B1 (MAJOR, fail-OPEN)** — `_raw_hidden_basis_concerns` asked "does the approver see ANY claim-state record ever" (semantic_claim_state is an APPEND family), so a benign visible `CURRENT` masked a later compartmented `RETRACTED`.
- **B-1 durability (MAJOR)** — `put_payload`'s "atomic" temp lived INSIDE `payload_dir`; `export_to` copies it wholesale → `import_from` rejects the non-digest name → the A-F2 harm (unrestorable backups, no signal) reintroduced on a crash OR a concurrent write (~5% of backups). B-2 (MAJOR): the loud `get_payload` bypassed `provenance.py`'s designed `(KeyError,FileNotFoundError,OSError)` fallback → 500 on `/api/evidence`. B-3/5/6 MINOR. Chokepoint 4 (challenge/scrub) CONFIRMED, held.

**Repair (this round):**
- Ck1: `record_transition`/`marked_for_subject` now floor on the subject AND on EVERY object cited in `evidence_refs` (resolved to its current marking via a new `_reference_markings` over claims + all analytic families + activity/execution). The rule is "cite what you quote" and the floor is automatic; leaking sites now cite the indicator / subject-forecast / assumption. A4 fixed by referencing the indicator by (scrubbed) id, not embedding its text (a PUBLIC forecast must not be reclassified just because a SPECIAL indicator watches it). The property test now asserts `marking ≥ join(subject, ALL references)`, driven through the SHIPPED `check_indicators`/`try_machine_resolution`/`refresh_warnings` paths — the invariant the class REQUIRES, not the one the code already enforced.
- Ck2: the gate compares the CURRENT claim-state's visibility (`can_view(current_state.marking, approver)`), not "any state ever visible"; lock strengthened with the benign-visible-then-hidden-retraction masking case.
- Ck3: `put_payload` temp is written OUTSIDE `payload_dir` and `export_to` copies only 64-hex names (B-1); `provenance.py` catches the loud StoreError and falls back to the verified custody copy (B-2); the delta import installs payloads PER APPLIED EVENT after the divergence check (B-3); `_scan_digests` walks dict keys (B-5); put/get_payload type the directory case (B-6).

Broad sweep 5095 passed / 0 regressions. Residuals for the NEXT fresh review: B-4 (import cannot prove referential completeness generically — an honest bundle is complete, a hostile omission surfaces at read as FileNotFoundError); the SEMANTIC-plane derived records (`changes.py`) were not re-audited this round; B2 is an accepted fail-closed disclosure (a control that blocks necessarily signals "there is a blocker"). Per the stopping condition, THIS repaired diff must itself go to fresh confirmatory attack before merge.

## Round 25 — CONFIRMATORY ATTACK on `06060c0` broke it again; repaired

Two independent Grok reviewers (A = marking/gate, B = durability) attacked `9d0760d`+`06060c0`. **BOTH returned DIFF_BROKEN.** The same two traps: a class-level test that only saw ids the code already cited, and a fix applied to transitions/one import family while the sibling (object versions / `export_to` / `import_from`) kept the old shape.

- **R25A-1 (CRITICAL)** — A4 sibling. `APPLY_PROBABILITY` wrote the SPECIAL indicator's `rationale` into the PUBLIC forecast version (`change_reason` / `probability_basis`). The `PROBABILITY_UPDATED` transition cited the indicator and was floored; `append_version` does not floor a forecast on mere `indicator_ids` (A4: association is not embed). Lock: `test_apply_probability_does_not_embed_indicator_rationale`.
- **R25A-2 (CRITICAL)** — resolver hole. `_reference_markings` (and the twin `forecasts._ref_markings`) resolved claims + analytic families + activity/execution, not observations/manifestations/changes/hypotheses. A PUBLIC wildcard indicator firing on a SPECIAL observation cited the observation id and quoted its value; the floor contributed nothing. The lock test's `mark_of` was analytics+claims, so the hole was invisible. Repair: one shared `resolve_reference_markings` covering those families; `_fire` / `INDICATOR_FIRED` no longer embed observation values or indicator descriptions; indicator `_reappend` accepts `reference_markings`. Lock: `test_fire_does_not_embed_observation_value`; property test now indexes observations.
- **R25A-3 (MAJOR)** — `retrieve_analogues` quoted a SPECIAL episode title into a PUBLIC `RETRIEVED` transition and did not floor the analogue record on the episode. Repair: cite `episode_id`; `append_version` floors `historical_analogue` on the embedded episode (association such as `forecast.indicator_ids` is still not a floor — A4). Lock: `test_retrieve_analogues_cites_episode_not_its_title`.
- **R25A-4 (MAJOR)** — Ck2 only intersected cited `basis_refs` with `semantic_claim_state.claim_id`. A SUPPORTED sentence citing the observation of a retracted claim shipped. Repair: `_related_claim_ids` walks observation → claim and manifestation → observation → claim on the RAW store. Lock: `test_hidden_retraction_via_cited_observation_blocks_approval`.
- **R25B-1 (MAJOR)** — `export_to` `copyfile`d a torn 64-hex slot and wrote `payloads[name]=name` without hashing. Backup succeeded; `import_from` then refused (unrestorable, no signal at backup time). `build_delta_bundle` already used `get_payload` (sibling unguarded). Repair: `export_to` copies through `get_payload` (torn → `StoreError`); skips symlinks; `put_payload` takes the append lock. Lock: `test_export_refuses_a_torn_payload`.
- **R25B-2 (CRITICAL)** — `import_from` wrote `store_meta` + `events.jsonl` then raw payload `write_bytes`. A crash after the log left a dest that OPENed, refused retry, and whose later export dropped the missing payload. Repair: install into a sibling staging dir (payloads first, `store_meta` last) and rename; dest without `store_meta` is not a store and is overwritten. Lock: `test_import_retries_over_a_dest_without_store_meta`.
- **R25B-3 (MINOR)** — 64-hex symlink followed by `is_file()`. Closed by the no-symlink + `get_payload` hash check.
- **R25B-4 (MINOR)** — delta `json.loads` lacked `_no_duplicate_keys`. Closed; NaN did not land (re-canonicalized) but the parse is now strict.

Self-review sibling before the next independent round: `collect.py` collection-need text no longer embeds an indicator description (id only). Semantic-plane watch/`_propagate` was re-probed in Round 25-A and HELD. B-4 (hostile omission of a payload from an otherwise valid bundle) remains an accepted LIMITATION: fail-closed at `get_payload`, not silent-wrong evidence. A hand-built dest that already has `store_meta`+events and no payloads is not a state the new importer can mint; `get_payload` is still fail-closed at read.

## Round 26 — WHOLE-TRANCHE attack broke the Round-25 repair; repaired

Two independent Grok reviewers attacked `5b5125a` across the whole tranche. **BOTH returned DIFF_BROKEN.**

- **R26A-1 (CRITICAL)** — Ck2 still fail-open for every `validate_report` family except claim/observation/manifestation. Citing a PUBLIC forecast/hypothesis/theme/warning/path/indicator whose supporting claim was later SPECIAL-RETRACTED shipped. Repair: `_related_claim_ids` walks `material_claim_ids`, embedded analytic ids, and reverse edges (objective←assumption/path, forecast←warning). Lock: `test_hidden_retraction_via_cited_forecast_blocks_approval`.
- **R26A-2 (CRITICAL)** — `collect.py` still interpolated theme title, narrative statement, forecast question, interest statement, edge note into PUBLIC discriminator/requirement text. Indicator description was the only site Round 25 closed. Repair: cite object ids only. Lock: `test_forecast_uncertainty_becomes_collection` asserts the question is absent from the need text.
- **R26A-3 (MINOR)** — `resolve_reference_markings` first-matched `records_of` for versioned families (PUBLIC v1 masked SPECIAL v2). Repair: latest-wins / `latest_by_id`.
- **R26A-4 (MINOR)** — APPLY_PROBABILITY passed SPECIAL observation ids as `evidence_refs`, flooring the PUBLIC forecast version (A4 over-classify, no text leak). Repair: forecast fire path cites the indicator only.
- **R26B-1 (MAJOR)** — `export_to` `continue`d on 64-hex symlink/dir/fifo, so a live store that could still serve evidence produced a successful backup with `payloads={}`. Repair: non-regular 64-hex slot → `StoreError`; `put_payload` replaces a symlink with a regular file. Lock: `test_export_refuses_a_symlink_payload_slot`.
- **R26B-2 (MAJOR)** — dest-side `copyfile`/`write_bytes` followed a pre-planted symlink (arbitrary write as the export uid). Repair: `_export_write_bytes` refuses symlink/non-file dest members. Lock: `test_export_refuses_a_dest_symlink`.
- **R26B-3 (MAJOR)** — `import_delta_bundle` applied a good prefix then planted payloads from a later rejected event (unknown type / bad entry_hash). Repair: `preflight_imported_events` validates the whole suffix before any `put_payload`/`append`.
- **R26B-4 (MINOR)** — empty/torn `store_meta.json` untyped-failed on open and blocked retry. Repair: typed `StoreError` on open; `_looks_like_complete_store` required for the retry gate. Lock: `test_import_overwrites_dest_with_torn_store_meta`.

B-4 remains an accepted LIMITATION (hostile omission fail-closed at `get_payload`). Semantic-plane watch HELD. Round-25 locks still pass.

## Round 27 — WHOLE-TRANCHE attack found the next hop; repaired

Two independent Grok reviewers attacked `cf7d685`/`8a36d87`. **BOTH DIFF_BROKEN.**

- **R27A-1 (CRITICAL)** — Ck2 did not follow `proposition_refs` of kind hypothesis/theme (the workbench `author_forecast` shape). Repair: `_claims_from_record` queues every `(kind, id)` pair.
- **R27A-2 (CRITICAL)** — collection persist still wrote SPECIAL `desired_subject_ref` into a PUBLIC discriminator. Repair: `open_analytic_requirements` floors persist marking on `claim_markings(need.claim_ids)`.
- **R27A-3 (MAJOR)** — sentence `assumption_ids` were omitted from the Ck2 cited set. Now unioned.
- **R27A-4 (MAJOR)** — `validate_report` STALE_BASIS only looked at SUPPORTED claims. Now walks related claims of inferential citations (visible half; hidden half remains Ck2).
- **R27B-1 (MAJOR)** — dest-side symlink writes on `build_delta_bundle` / `build_pace_bundle` / `create`. Repair: shared `_export_write_bytes`; refuse symlink `payloads/` dest dir.
- **R27B-2 (MAJOR)** — delta apply dropped the append lock between events. Repair: `apply_imported_events_locked` holds one lock across preflight + payloads + commits.

## Round 28 — WHOLE-TRANCHE attack found the next hop; repaired

Two independent Grok reviewers attacked `9a77e9a`. **BOTH DIFF_BROKEN.**

- **R28A-1 (CRITICAL)** — STALE_BASIS walk lived inside `if status == "SUPPORTED"`. Legal inferential cites (`EXPLICITLY_INFERENTIAL`) never ran it. Repair: run the walk for SUPPORTED and EXPLICITLY_INFERENTIAL. Lock: `test_visible_retraction_on_inferential_forecast_blocks_approval`.
- **R28A-2 (CRITICAL)** — collection persist floored only on `need.claim_ids`, not the SPECIAL source indicator/forecast whose `desired_subject_ref` was copied. Repair: also `resolve_reference_markings(source_id)`.
- **R28A-3 (CRITICAL)** — Ck2 review half checked only cited + related *claims*, not intermediate walk nodes (hypothesis a forecast `proposition_refs`). Repair: pass `walked` into the review check.
- **R28A-4 (MAJOR)** — `append_version` did not floor a forecast on non-claim `proposition_refs`. Repair: `_embedded_analytic_ids` for `analytic_forecast`.
- **R28B-1 (MAJOR)** — `create()` followed a `payloads/` directory symlink. Repair: refuse.
- **R28B-2 (MAJOR)** — `recover_torn_tail` dest-side dangling symlink write. Repair: `_export_write_bytes` + refuse symlink dests.
- **R28B-3 (MAJOR)** — delta apply preflighted events but not dest payload slots; a directory on digest 2 aborted after event 1 committed. Repair: preflight every payload slot before any commit.

## Round 29 — WHOLE-TRANCHE confirmatory attack (pending)

The Round-28 repair is itself un-attacked. Same stopping condition.

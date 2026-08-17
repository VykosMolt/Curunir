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

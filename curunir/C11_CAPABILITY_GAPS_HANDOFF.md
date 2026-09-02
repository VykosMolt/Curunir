# C11 capability gaps — actionable targets

The C11 semantic review found five declared capabilities that no mapped test calls.
One is now closed; four remain. Each is a small, self-contained job. Nothing here
requires reading the readiness campaign.

## Done

**C11-F11 access-aware collaboration.** `tests/test_c11_f11_access_capability.py`
exercises `curunir_operational/access.py::can_view` and `visible` directly:
every denial axis has a deny case and a matching allow case, so it cannot pass by
denying everything. Verified causally bound — neutralising `can_view` at runtime
fails 10 of its 11 tests (the survivor is the `Marking` construction check, which
correctly does not route through the access decision). Marked `no_db`.

## Remaining, in rough order of product value

1. **C11-F01 lawful acquisition.** `curunir_operational/policy.py::classify_access`
   and `acquisition_eligible` are imported but never called; `connectors.py` is
   never imported. Test shape: for each access class, one eligible and one
   ineligible acquisition, asserting the decision and that an ineligible one is
   refused rather than silently downgraded.

2. **C11-F12 revocation.** No mapped file references revocation at all, and
   `revoked_at` returns `False` on all 27 calls, so a permissive stub is
   indistinguishable from the real thing. Test shape: an identity that IS revoked,
   asserting the revoked path is taken. Note: a stub returning `None` is falsy
   exactly like `False`, so a severance probe here is vacuous unless the test
   asserts on a genuinely revoked case — that mistake was already made once.

3. **C11-F03.** `curunir_operational/v5_3/predict.py` is never imported by its
   family's tests.

4. **C11-F14.** `sovereignty.py::run_exit_test` is never imported by its family's
   tests.

5. **C11-F10** has no evidence of its own: no exclusively-owned mapped test file
   and no exclusive product function; all three of its mapped files also belong to
   F09 or F15. It needs at least one test that is about F10 and nothing else.

## Also worth knowing

- The pre-V6.1 baseline verified these families with **18 bespoke capability
  drivers** that exercised capability semantics directly. They were never retained.
  Until something replaces them, "capability preservation" cannot be claimed —
  only "the mapped checks still pass".
- Four mapped test files are 100% skipped in the default arm
  (`test_source_origin_v2.py` 113, `test_public_source_identity.py` 23,
  `test_entity_merge.py` 5, `test_operational_v3_stress.py` 1). Anything relying on
  them for coverage is relying on nothing.

## Live defects found along the way (independent of C11)

- `measure_d25_v6_wired.py:181` — `--reference` defaults to V5 while the pin uses
  V7. A prior seat reported a phantom D25 = 8/15 because of it. ~24 copies exist.
- Five inherited emitters write the substrate identity as the string literal
  `ef6e854c` into published evidence, asserting false provenance.
- A test mutates the campaign evidence tree as a side effect, making the artifact
  tree non-deterministic w.r.t. test execution and manufacturing false custody
  alarms. Fix: assert over `tmp_path` or an in-memory record.
- `v4/mutation.py::write_security_review` carries the same fabricated shape that
  was relabelled elsewhere (`threat_count` / `tests_run` / `failed: 0`), with its
  own consumers. A decision to register it was recorded and never executed.

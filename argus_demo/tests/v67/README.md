# V6.7 — the security tranche

Tests for the clean rewrite: information-flow integrity, authoritative control
gates, cryptographic identity, guarded egress, bounded hostile input, durable
recovery, and reproducible reconstruction. The contract these are written
against is `../../CURUNIR_V6_7_ARCHITECTURE.md`; the invariants are
`../../CURUNIR_V6_7_INVARIANTS.json`.

| Module | Covers |
|---|---|
| `test_marking_*.py` | no-write-down, across every axis, as a property |
| `test_authoritative_report_controls.py` | report gates read raw state and fail closed |
| `test_identity_crypto.py` / `_server.py` / `_signed_operations.py` | Ed25519, sessions, signed acts |
| `test_store_integrity.py` | hash chain, torn-tail recovery, delta import |
| `test_backup_restore_access.py` | export/import/restore under access control |
| `test_egress_and_resources.py` | SSRF guard, resource caps, limit-truthfulness |
| `test_browser_canonical_parity.py` | JS and Python produce identical bytes |
| `test_clean_reconstruction.py` | rebuild from committed bytes + pinned kernel |

## `HISTORICAL_EXPLOIT_CORPUS.json`

The defects that were actually found, in machine-readable form. Each entry is a
real exploit from the review rounds, retained so a repair cannot quietly
regress. Three independent adversarial reviews produced these: two returned
`REJECT`, the third `ACCEPT` — the ledger is
`../../CURUNIR_V6_7_INDEPENDENT_REVIEW.md`.

This corpus is evidence. Do not prune an entry because it now passes; that it
passes is the point.

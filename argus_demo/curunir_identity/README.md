# Curunír identity — Ed25519 actor identity and signed actions

The layer that makes an authoritative act attributable to a specific human and
verifiable long after the fact, by someone who was not there.

Built for V6.7 under `../CURUNIR_V6_7_ARCHITECTURE.md` §7: verification is pure,
authorization runs next, and the signed-action audit record is committed **only
after both succeed**. A signature that verifies but is not authorized leaves no
trace of success.

## Modules

| Module | Responsibility |
|---|---|
| `crypto` | The only module that touches raw Ed25519. Nothing else imports the primitive. |
| `contracts` | Replayable record types for enrollment, sessions and signed actions |
| `registry` | Public-key ownership and lifecycle: one owner per key family, atomic bounded enrollment, revocation |
| `sessions` | Challenge-response authentication and short-lived sessions, both bounded |
| `actions` | Canonical verification and durable attribution of a signed action |
| `replay` | Independent re-verification of retained signatures from the log alone |

## What it guarantees

- **The caller does not choose the identity.** The signed target binds actor,
  mission (`store_id`), resource and version. Presenting a signature for a
  different target is a refusal, not a downgrade.
- **Refusal order is fixed.** Expiry, signature validity and actor match are
  checked in a defined order so failure modes cannot be probed apart.
- **Capacity pressure cannot evict someone else.** A newcomer at capacity is
  refused when it owns no eviction candidate; it cannot displace another
  principal's challenge or session. A capacity refusal restores the verified
  challenge so the same nonce succeeds once space frees.
- **Keys never write down.** Key material and enrollment records obey the same
  marking floor as every other record.
- **Replay needs no secret.** `replay` re-verifies retained signatures from
  public keys in the log, offline.

## Boundary

This package authenticates and attributes. It does **not** decide what an actor
is allowed to do — that is `curunir_operational.access` and, for report gates,
`curunir_workbench.authority`. Verifying a signature is never sufficient for an
action to proceed.

## Tests

```bash
python -m pytest tests/v67/test_identity_crypto.py \
                 tests/v67/test_identity_server.py \
                 tests/v67/test_identity_signed_operations.py -q
```

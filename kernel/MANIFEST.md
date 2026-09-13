# kernel/ — manifest

Kernel Gold. Rules: `../HOUSEKEEPING.md`. `README.md` has the pin and the
restore procedure.

## Belongs here

| Path | Tracked | Role |
|---|---|---|
| `argus/` | no (mounted, hash-verified) | the pinned ARGUS kernel, 140 `.py` files, `4c173df7…`. **Read-only.** |
| `argus_kernel_pinned_4c173df7.tar.gz` | no | the only backup of that tree. Never delete; replace only together with a new pin. |
| `README.md`, `MANIFEST.md` | yes | orientation |
| `schema.sql` | no (excluded) | hash-checked by `curunir/tests/test_operational_protection.py` |

Everything else the kernel plane once held (Postgres kit, review app, corpus,
inboxes, docs, research tests) lives in the Argus repository (called Saulot until 2026-09-02).

## Does not belong here

- Anything inside `argus/` that is not one of the 140 pinned `.py` files.
  `__pycache__` is tolerated by the identity check; nothing else is.
- Product code, product docs, campaign output roots.
- Trained weights of any kind.

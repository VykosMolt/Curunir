# kernel/ — manifest

Kernel Gold. Rules: `../HOUSEKEEPING.md`. `README.md` has the pin and the
restore procedure.

## Belongs here

| Path | Tracked | Role |
|---|---|---|
| `argus/` | no (mounted, hash-verified) | the pinned ARGUS kernel, 140 `.py` files, `4c173df7…`. **Read-only.** |
| `argus_kernel_pinned_4c173df7.tar.gz` | no | the only backup of that tree. Never delete; replace only together with a new pin. |
| `README.md`, `MANIFEST.md` | yes | orientation |
| `tests/` | mixed | the ARGUS research-line suite, `fixtures/`, `kernel_fuzz_support.py`, the Postgres `conftest.py` |
| `conftest.py`, `pytest.ini` | yes | puts this root on `sys.path` |
| `schema.sql`, `docker-compose.yml`, `.env.example` | no (excluded) | the Postgres spine; `schema.sql` is hash-checked by `curunir/tests/test_operational_protection.py` |
| `.env` | no, never | local credentials |
| `review_app/` | no | the read-only FastAPI review UI |
| `demo_corpus/` | no | corpus intake (`raw/`, `manifest.csv`) |
| `exports/` | no (ignored) | demo provenance bundles |
| `content_store/`, `offline_source_inbox/`, `review_inbox/` | no | content-addressed bytes, offline official-source intake, frozen review packets |
| `docs/` | no | ARGUS-era design notes, findings, prospective-intelligence manifests — historical |

## Does not belong here

- Anything inside `argus/` that is not one of the 140 pinned `.py` files.
  `__pycache__` is tolerated by the identity check; nothing else is.
- Product code, product docs, campaign output roots.
- Trained weights of any kind.

## Known state of the tests

The `*_artifacts.py` modules and `test_review_queue_repair_v1.py`,
`test_prospective_v3_operations.py`, `test_v3_external_commitments.py`,
`test_prospective_v2_hardening.py` assert over July-2026 pilot artifact roots
that were purged on 2026-09-02. They fail with `FileNotFoundError` until they
are deleted with a ledger line (`tests/README.md`).

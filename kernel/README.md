# Kernel Gold — the pinned ARGUS kernel

`argus/` is the inherited ARGUS claim kernel that Curunír is built on. It is
**external to the product by design**: Curunír imports exactly eight of its
modules (enumerated in `../curunir/CURUNIR_RECONSTRUCTION.md`), and the whole
tree is identified by one hash so that nobody can quietly build against a
different kernel.

| | |
|---|---|
| Pinned tree | `argus/` — 140 Python files, nothing else may live inside it |
| `argus_kernel_tree_sha256` | `4c173df7412952b8318b7838a06ee991638fd9144eee2be36232c64aecfb7906` |
| Backup | `argus_kernel_pinned_4c173df7.tar.gz` — the only other copy of that tree. Keep it. |
| Pinned by | `../curunir/CURUNIR_V6_7_RECONSTRUCTION.json` and `CURUNIR_V6_8_REPOSITORY_TRUTH.json` |

**Do not edit anything under `argus/`.** The hash covers every `.py` file, path
and content; the reconstruction test, the V6.7 validator and the V6.8 pilot
harness all recompute it and refuse to proceed on a mismatch. A non-`.py` file,
a symlink, or a hard link inside the tree is also a refusal.

## Verify the mounted tree

```bash
cd ../curunir
PYTHONPATH="$PWD:$PWD/../kernel" .venv/bin/python - <<'PY'
from tools.reconstruct_v67 import kernel_identity
from pathlib import Path
h, n, _ = kernel_identity(Path("../kernel/argus"))
print(h, n)   # expect 4c173df7… and 140
PY
```

## Restore it from the tarball

```bash
cd kernel
rm -rf argus && tar xzf argus_kernel_pinned_4c173df7.tar.gz   # unpacks argus/
```

The tarball's top-level entry is `argus/`, so it unpacks straight into place.
Unpack as a real directory, never a symlink — the identity check rejects one.

## How the product finds it

Curunír resolves the kernel from `CURUNIR_ARGUS_KERNEL` (the `argus` package
directory itself) or, by default, `../kernel/argus` relative to the product
root. `curunir/conftest.py` puts `kernel/` on `sys.path`; `tools/validate_v67.py`
and `tools/reconstruct_v67.py` take `--kernel`. Before 2026-09-02 the tree was
mounted inside the product root as `argus_demo/argus/`; the manifests were
revised to `kernel/argus` that day and the harness verifies the hash, never
the path.

## What else is here

Only `README.md` and `MANIFEST.md` are tracked. `argus/`, the tarball and
`schema.sql` (hash-checked by `curunir/tests/test_operational_protection.py`)
sit beside them untracked. The kernel's Postgres spine, demo kit, docs and the
ARGUS research-line tests stayed in the Argus repository (`/home/moloch/Argus`, called Saulot until 2026-09-02) (`kernel/` there).

## The ARGUS demo kit (the original kernel README, kept for reference; the kit itself lives in the Argus repository, `~/Argus/kernel`)

A **Postgres-first, bitemporal, mention-grounded, evidence-span-backed claim
kernel** for **public-source** AI / regulatory intelligence.

This is a **demo spine, not a Palantir clone.** It exists to prove one thing
works end to end:

```
source → document → document_version → evidence_span → mention
       → entity (resolved) → claim → claim_relation
       → verification / action log → case / export
```

Everything is deliberately small. There is no graph database, no message bus, no
generic "action framework," and no profiling/surveillance logic (see
[Scope boundaries](#scope-boundaries-what-is-deliberately-not-here)). The point
is to get the **data model and the write discipline** right first.

---

## What the schema proves

1. **Provenance is structural, not a comment.** A claim cannot exist without an
   `evidence_span`, and an evidence span stores the **exact quote** plus
   surrounding context and a `quote_hash`. You can always answer "where did this
   come from?" down to the characters in a specific `document_version`.

2. **Claims are grounded in *mentions*, resolved to *entities* separately.** A
   claim points at the surface `mention` it was extracted from. Which real-world
   entity that mention refers to is a *separate, revisable* decision. The
   entity id on a claim version is a **derived cache**.

3. **Identity is revisable without rewriting history.** Merge two duplicate
   entities, or split one back out, and every affected claim's
   `subject_entity_id` / `object_entity_id` is **re-derived** — by closing the
   old claim version (`tx_to`) and inserting a new one, never by overwriting.
   The old version is still there.

4. **Verification is a state machine with an audit trail.** Changing a claim's
   `verification_state` (`unverified → human_verified / disputed / rejected …`)
   closes the current version and inserts a new one, plus a `claim_events` row
   tied to the `action_log` entry that caused it. **Claims are never
   auto-verified** — extraction output is born `unverified` (or, at most,
   `machine_supported`).

5. **Every state change is one transaction + one audit row.** All writes go
   through hardcoded handlers in [`argus/actions.py`](argus/actions.py). Each
   appends exactly one `action_log` row inside the same transaction. Nothing in
   `db.py`, `cli.py`, `export.py`, or the review UI writes domain data directly
   (enforced by a test).

Structural invariants are guarded by the database itself with partial unique
indexes: **exactly one current version** per entity and per claim
(`tx_to is null`), and **at most one active assignment** per mention.

---

## Layout

```
kernel/
  schema.sql            # the whole data model (tables, guards, indexes, views)
  docker-compose.yml    # one Postgres service, persistent volume, nothing else
  requirements.txt
  .env.example
  argus/
    db.py               # connections + transaction() + init/reset schema (NO domain writes)
    schemas.py          # enum vocabularies + validators + ActionResult
    actions.py          # the ONLY place domain data is written
    extract.py          # deterministic mention *proposer* (not an LLM; never writes)
    export.py           # read-only provenance bundle builder
    cli.py              # python -m argus.cli ...  (incl. `demo`)
  tests/                # pytest proofs (schema, actions, entity merge)
  demo_corpus/          # where the REAL corpus goes later (raw/ + manifest.csv)
  review_app/main.py    # optional, minimal read-only FastAPI review UI
```

---

## Quick start

Everything below is run from inside `kernel/`, using the shared virtualenv at
`../curunir/.venv` (activate it, or prefix commands with `../curunir/.venv/bin/`).

### 1. Start Postgres

```bash
cd kernel
cp .env.example .env          # defaults: user/pw/db = argus, host port 5544
docker compose up -d
```

This is the only service. Data persists in the `argus_pgdata` volume.

### 2. Use the shared Python env

The one virtualenv lives in `../curunir/.venv` and already carries `psycopg`,
`fastapi` and everything else the kernel needs (its pins are in
`../curunir/requirements.txt`, which is hash-locked — do not edit it).

```bash
source ../curunir/.venv/bin/activate
```

### 3. Initialize the schema

```bash
python -m argus.cli init-db          # or: init-db --reset to wipe & recreate
```

### 4. Run the tests

Tests use a **separate** `argus_test` database (created automatically) so they
never touch demo data:

```bash
python -m pytest -q
```

### 5. Run the tiny demo flow

```bash
python -m argus.cli demo
```

This loads the two fixture documents (`tests/fixtures/doc_a.txt`, `doc_b.txt`) —
a regulator fine that is later **amended/superseded** — and walks the whole
spine: ingest → spans → mentions → entities → assignment → two claims → a
`supersedes` + `contradicts` relation → dispute the superseded claim → **merge a
duplicate entity (which re-derives the affected claim's entity cache)** → build a
case → export a provenance-complete JSON bundle to `exports/demo_case.json`.

### 6. (Optional) Browse in the review UI

```bash
uvicorn review_app.main:app --port 8077
# open http://127.0.0.1:8077/  (and /docs for the API)
```

Read-only, except `POST /claims/{id}/state`, which routes through the
`set_claim_verification_state` action handler.

---

## CLI reference

```text
init-db [--reset]
ingest-text   --path PATH --title TITLE --source NAME
create-span   --document-version-id ID --start N --end N        # or --quote TEXT
create-mention --document-version-id ID --span-id ID --surface TEXT --type TYPE
create-entity --type TYPE --name NAME
assign-mention --mention-id ID --entity-id ID
create-claim  --text TEXT --type TYPE --predicate PRED --evidence-span-id ID \
              --subject-mention-id ID [--object-mention-id ID]
set-claim-state --claim-id ID --state human_verified|disputed|rejected|machine_supported|unverified --reason TEXT
relate-claims --src ID --dst ID --relation contradicts|supersedes|duplicates
merge-entities --source ID [ID ...] --target ID [--reason TEXT]
unmerge-mention --mention-id ID --entity-id ID [--reason TEXT]
create-case   --title TITLE [--description TEXT]
add-case-item --case-id ID --item-type claim|document|entity|relation|note --item-id ID --note TEXT
export-case   --case-id ID --out PATH
load-manifest --manifest demo_corpus/manifest.csv [--raw-root demo_corpus/raw] \
              [--allow-duplicates] [--dry-run] [--out exports/corpus_load_report.json]
corpus-report [--out PATH]                      # read-only coverage report
propose       --document-version-id ID         # read-only candidate mentions
demo                                            # the end-to-end flow above
```

`load-manifest` ingests **only** sources / documents / document_versions from a
manifest CSV (no claims/mentions/entities). Columns: `doc_id,title,source_name,
source_type,source_url,publisher,jurisdiction,language,document_type,
published_date,local_path,topic_tags,notes` (see `demo_corpus/manifest.csv.example`).
Per-row status is one of `ingested | dry_run | missing_file | unsupported_type |
duplicate_skipped | error`; duplicates are detected by `raw_hash`.

Each state-changing command prints the `action_log_id` it wrote, plus the ids it
created.

---

## Scope boundaries (what is deliberately *not* here)

**Not built, on purpose** — this kernel is for reasoning about *public records and
their provenance*, not people:

- ❌ No biometric/face/person re-identification, tracking, or movement traces.
- ❌ No private-person profiling, threat scoring, watchlists, or social-credit logic.
- ❌ No surveillance/alerting tooling.

**Not wired up yet** (out of scope for the spine, not forbidden):

- No OpenSearch / Neo4j / Memgraph / Gaffer / OSIRIS / OpenCTI / MISP.
- No Kafka / Airflow / NiFi / Iceberg / Spark.
- No generic action/permission framework — actions are hardcoded and boring.
- No real LLM extractor. `extract.py` is a deterministic regex *proposer*;
  proposals must be persisted via the explicit creation actions and are never
  auto-verified. A real pipeline would replace it with an NER/LLM stage that
  writes to a staging table, keeping the same "propose, don't auto-verify"
  contract.
- PDF/HTML extraction (plain text only for now; PDF support should fail
  gracefully when added).
- AuthN/AuthZ. `actor_id` / `actor_role` / `policy_decision` columns exist so
  policy can be layered on later; the demo passes them through but enforces
  nothing.

---

## Next real step

Put **~20 hard, genuinely contested** DPA / regulatory / enforcement documents
into [`demo_corpus/raw/`](demo_corpus/raw/) and fill out `manifest.csv` (start
from [`manifest.csv.example`](demo_corpus/manifest.csv.example)). Good candidates:
decisions that were later **amended, appealed, partially annulled, or
contradicted** across sources — exactly the cases where bitemporal versioning,
`claim_relations`, and entity merge/unmerge earn their keep.

Then build a small `load_manifest` flow (over the existing actions) and start
asking the hard questions: *which claims are superseded? which contradict across
regulators? what changed between document versions?*

---

## Design notes / gotchas

- **Logical id vs version id.** `entities.id` / `claims.id` are stable logical
  ids. Version rows (`entity_versions.version_id`, `claim_versions.version_id`)
  are their own ids. The two are never conflated.
- **`tx_*` vs `valid_*`.** `tx_from/tx_to` is transaction time (when a row was the
  known-current version). `valid_from/valid_to` is valid time (when the fact holds
  in the world). Re-derivation and verification changes move `tx_*`; they don't
  touch `valid_*`.
- **Derived caches must be re-derived, not trusted.** `claim_versions.subject_entity_id`
  / `object_entity_id` are a convenience cache of how the anchoring mentions
  resolve *right now*. Any merge/unmerge/assignment recomputes them — see
  `_rederive_claims_for_mentions` in `actions.py`.
- **Audit ordering.** Within an action that emits child events, the `action_log`
  row is inserted first so `claim_events.action_log_id` / resolution events can
  reference it; its `result_json` is finalized at the end of the same
  transaction.

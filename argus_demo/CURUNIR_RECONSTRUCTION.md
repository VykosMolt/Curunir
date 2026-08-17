# Curunír — Clean-Checkout Reconstruction (V6.7 §7)

What a fresh environment needs to reconstruct and run the **supported Curunír
product** from tracked repository state, and what is deliberately external. The
required property is *reproducibility of the supported dependency graph* — not
that every file live in one Git repository.

Proven by `tests/test_clean_reconstruction.py`: it exports the tracked tree with
`git archive` (committed files only), confirms the external boundary, mounts the
kernel snapshot, and runs a real reconstruction (import → create store → export →
import → replay) in that isolated tree with an import path that is the isolated
tree **alone** — so nothing resolves to the working tree, a home-directory file,
or an accidentally-installed module.

## What is tracked (the product)

The committable product is the `curunir_*` packages and `tests/`:
`curunir_fabric`, `curunir_semantic`, `curunir_analytic`, `curunir_workbench`,
`curunir_operational`, `curunir_identity` — plus the workbench's static frontend
(`curunir_workbench/static/`). A clean checkout reconstructs all of this.

## What is deliberately external (documented prerequisites)

`.git/info/exclude` intentionally untracks these; they are not development-machine
accidents but named external prerequisites:

### 1. The inherited `argus` kernel (a versioned snapshot, mounted at `argus/`)

The tracked product imports **exactly these 8 kernel modules** (funnelled almost
entirely through the single `curunir_operational.canonical` seam, per the
sovereignty manifest):

- `argus.prospective.freezing` — canonical serialization / hashing (the byte
  contract the whole store and the signed-identity layer depend on)
- `argus.source_intelligence.models` — `digest_id`, `stable_json`, `require_aware`
- `argus.source_intelligence.custody` — evidence custody preservation
- `argus.source_intelligence.policy` — access classification
- `argus.source_intelligence.registry` — source registry types
- `argus.public_web_transport_v4` — the guarded public-web transport
- `argus.prospective.research.offline_official_ingestion` — the kernel XML guard
- `argus.extract` — `propose_mentions`

The kernel ships as a **separate versioned tarball snapshot**, mounted at
`argus_demo/argus/`. Its dependency-surface identity (sha256 over the 8 module
files above, sorted) at this product revision is:

```
kernel_dependency_surface_sha256 = 734b367096d9c37388dc66d88d3193e810fa8be393074f377ae0d0a52804269d
```

Pin/verify this hash when mounting the snapshot; a mismatch means the kernel the
product was built against is not the one being mounted. (Do not vendor the kernel
into this repo — it is a large inherited codebase owned elsewhere; mounting the
identified snapshot is the supported model.)

### 2. Python runtime dependencies (PyPI, pinned in the untracked `requirements.txt`)

Imported by the tracked product: **`cryptography`** (Ed25519 identity — pinned
`cryptography==44.0.0`, added with sign-off this programme), **`fastapi`**,
**`uvicorn`**, **`pydantic`** (the workbench HTTP boundary). `playwright` is an
optional test-only dependency for the browser E2E journeys. Python ≥ 3.12.

### 3. Infrastructure / config (untracked, only some paths need them)

`conftest.py` + `pytest.ini` (test collection), `docker-compose.yml` + `schema.sql`
(the optional Postgres used only by DB-marked tests — the entire `curunir_*`
product runs file-backed without it; those tests skip), `.env.example`, `docs/`,
`demo_corpus/`, `review_app/`. None are required to run the product itself.

## Reconstruction steps

1. `git clone` / `git archive` the repository → the tracked `curunir_*` product.
2. Mount the `argus` kernel snapshot at `argus_demo/argus/` and verify its
   `kernel_dependency_surface_sha256`.
3. Create a Python ≥ 3.12 environment and install the pinned deps
   (`cryptography==44.0.0`, `fastapi`, `uvicorn`, `pydantic`; `playwright` for
   browser tests).
4. Run the product: create a `WorkbenchStore`, drive a mission, or launch the
   workbench (`uvicorn curunir_workbench.server:app` with `CURUNIR_MISSION_ROOT`
   + `CURUNIR_ACTORS`). Restore/replay of a mission needs **no network and no
   providers** — it is pure log replay.

## Boundary honesty

Fully-offline reconstruction depends on one genuinely external asset: the `argus`
kernel snapshot, which is not owned by this repository. Everything on Curunír's
side is reproducible from tracked state plus the pinned deps, and the kernel
dependency is explicit, enumerated, and identified by hash above — so the
supported dependency graph is reproducible even though the repo is not
self-contained by design.

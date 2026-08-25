# Tests

138 modules, ~36k lines, against ~33k lines of product. Run from `argus_demo`
with the repository root on `PYTHONPATH` and the ARGUS kernel mounted at
`argus/`.

```bash
PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

## Layout

| Path | Contents |
|---|---|
| `test_fabric_*.py` | collection: connectors, transport, SSRF guard, watches, planner |
| `test_semantic_*.py` | normalization, extraction, world model, change detection |
| `test_analytic_*.py` | themes, narratives, stakeholders, impact, forecasts, warnings, **model backends** |
| `test_operational_*.py` | store, access lattice, projection, custody, sovereignty, scenarios |
| `test_workbench_*.py` | command layer, projections, reports, replay, browser E2E |
| `test_curunir_v68.py` | the V6.8 pilot harness |
| `v67/` | the V6.7 security tranche — see `v67/README.md` |
| `*_support.py` | shared fixtures (`semantic_support`, `analytic_support`, `workbench_support`, `operational_support`) |
| `fixtures/`, `js/` | text fixtures; the JS canonical-parity harness |

## What to expect

The `curunir_*` product plane passes. The suite also collects the inherited
ARGUS research lines, whose corpora and models are not tracked here — those
account for every residual failure. The exact accepted set is pinned in
`../CURUNIR_V6_9_EXCISION.json`.

Two tests fail for environmental reasons and are not defects:

- `v67/test_clean_reconstruction.py` needs the kernel — set
  `CURUNIR_ARGUS_KERNEL` or mount it at `argus_demo/argus/`.
- `test_curunir_v68.py` **refuses a dirty checkout by design**. Commit first.

Postgres-marked tests skip unless a database is up on port 5544; the entire
`curunir_*` product runs file-backed without one.

## House rules

- **Never weaken a test to make a run pass.** Any change to what is collected
  is declared in a ledger, not absorbed.
- A test must fail when the mechanism it covers is disabled. Several suites
  carry explicit causal probes; if you add a guard, prove the test detects its
  removal.
- Assert the invariant, not the instance. Per-instance regression tests never
  converge on a defect class — find the chokepoint.

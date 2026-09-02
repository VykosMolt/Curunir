# Tests

64 modules plus the `v67/` tranche, against ~33k lines of product. Run from
`curunir` with the product root and the kernel plane on `PYTHONPATH` (the root
`conftest.py` does this for you when pytest is invoked from here).

```bash
PYTHONPATH="$PWD:$PWD/../kernel" .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

The ARGUS research-line suites are no longer collected here: the kernel tests
live in `../../kernel/tests/` and the neural/capsule tests in
`../../capsules/tests/`, each with its own README.

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

The `curunir_*` product plane passes. The accepted non-passing set recorded in
`../CURUNIR_V6_9_EXCISION.json` was measured when the research-line suites were
still collected alongside; those now live in the sibling planes.

Two tests fail for environmental reasons and are not defects:

- `v67/test_clean_reconstruction.py` needs the kernel — set
  `CURUNIR_ARGUS_KERNEL` or have it unpacked at `../kernel/argus/`.
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

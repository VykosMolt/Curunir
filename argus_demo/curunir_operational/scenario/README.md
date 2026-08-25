# Scenario — the synthetic Vessia Corridor fixtures

A deterministic, entirely notional relief-logistics scenario used to exercise
the operational plane end to end without touching a real source. **Never
imported by core**: the dependency runs one way, so nothing in the product can
accidentally rest on fixture data.

| Module | Contents |
|---|---|
| `config.py`, `v2_config.py` | scenario definition: objects, compartments, actors |
| `feeds.py`, `v2_feeds.py` | synthetic JSON / CSV / GeoJSON feeds |
| `questions.py` | the analytical questions the scenario must be able to answer |
| `runner.py`, `v2_orchestrate.py` | drive the full pipeline and emit a result bundle |
| `common.py` | shared helpers |
| `live_fixtures/` | two **preserved real GDACS captures** — genuine bytes, retained so the live path has a fixture that is not synthetic |

Everything here is explicitly notional. No claim produced from these fixtures
extends to a real person, company, place or regulator. `live_fixtures/` is the
one exception in provenance — those are real captured bytes — and even they are
scenario context, not an assertion about the world.

Run it:

```bash
python -m curunir_operational.cli run-scenario --store /tmp/v/store --out /tmp/v/out
```

Tests: `tests/test_operational_scenario.py`, `tests/test_operational_v2_*.py`.

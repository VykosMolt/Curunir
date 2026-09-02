# Curunír OSINT Fabric — foundation tranche

Persistent, source-aware public collection plane: `CURUNIR_V6_OSINT_FABRIC_FOUNDATION`.

The fabric turns the public-source research path into a durable collection
system built around one loop:

```
information need
→ which registered sources can help (capability-queryable registry)
→ typed multilingual discovery plan
→ policy-gated acquisition across genuinely different source mechanisms
→ immutable native evidence + manifestation lineage
→ coverage and failure accounting
→ pivots from acquired evidence
→ persistent watches that keep observing over time
```

## Architecture

One `FabricStore` root (a `MissionDataStore` superset: same hash-chained
append-only JSONL event log, plus fabric event types) carries the complete
lineage — mission requirements, source registrations, plans, executions,
manifestations, coverage, pivots, watches, change observations and alerts —
under a single verifiable chain with the existing export/import/replay
machinery. Raw acquired bytes are preserved through the established ARGUS
Source Intelligence custody path (`SourceCustodyStore` → content-addressed
`content_store/`, validated chain of custody), never a loose scrape directory.

| Module | Responsibility |
|---|---|
| `contracts` | fabric record ontology (validated frozen dataclasses) |
| `store` | `FabricStore`: mission store + fabric event types |
| `registry` | Global Source Registry: SI `SourceDescriptor` (bitemporal) + `CapabilityProfile` + status events; capability queries |
| `catalog` | starter catalog: Wikidata, GLEIF, SEC EDGAR FTS, Wayback, live web, Federal Register feed |
| `connectors/` | common acquisition boundary + one connector per acquisition shape |
| `variants` | deterministic multilingual name variants (transliteration, folding, local labels) |
| `planner` | need → typed, source-matched, budgeted `DiscoveryPlan` |
| `executor` | policy gate → connector → custody → `ManifestationRecord` + truthful `ExecutionRecord` |
| `pivots` | evidence-grounded `PivotEdge` proposals (reversible) + next-generation queries |
| `coverage` | per-need per-source coverage states incl. `NOT_SEARCHED` |
| `watch` | durable watch definitions, runs, typed change observations |
| `mission_bridge` | requirement↔need pairing; evidence-bound alerts from changes |
| `cli` | thin command layer |
| `demo` | bounded live demonstration (3 phases, real endpoints, real restarts) |

## Load-bearing semantics

- **Absence is not nonexistence.** Execution outcomes distinguish
  `EXECUTED_WITH_RESULTS / EXECUTED_EMPTY / SOURCE_FAILED / ACCESS_RESTRICTED /
  POLICY_REFUSED / RATE_DEFERRED / NOT_ATTEMPTED`; empty responses are still
  preserved in custody as evidence the search ran. Coverage keeps unqueried
  sources visible as `NOT_SEARCHED`, never silently covered.
- **Manifestations are distinct observations.** A live page and an archived
  capture of the same URL are different manifestations; re-retrievals link
  their predecessors through `prior_manifestation_id`.
- **Policy precedes transport.** `classify_access`/`acquisition_eligible`
  (Source Intelligence policy) run before any network call; refusals are
  recorded decisions, not downgrades.
- **Pivots are proposals.** Reversible by appending (latest-wins on replay);
  identity is never merged in this plane.
- **Watches are event-sourced.** `due_watches` is pure replay; a process
  restart loses nothing. Change observations are typed and evidence-bound;
  a feed record disappearing is recorded as window shift, not deletion proof.
- **Model participation is labelled.** Model-proposed queries/pivots carry
  `origin=MODEL` and pass the same matching and policy gates; only a HUMAN
  actor can answer or close a mission requirement (mission workflow rule).

## Quick start

```bash
cd curunir
P=.venv/bin/python
$P -m curunir_fabric.cli create-store --store /tmp/fabric/store
$P -m curunir_fabric.cli seed-catalog  --store /tmp/fabric/store
$P -m curunir_fabric.cli sources      --store /tmp/fabric/store --historical
$P -m curunir_fabric.cli open-need    --store /tmp/fabric/store \
    --mission m1 --question "Corporate identity of X?" --entity "X" --language en
$P -m curunir_fabric.cli execute      --store /tmp/fabric/store --need <NEED_ID>
$P -m curunir_fabric.cli coverage     --store /tmp/fabric/store --need <NEED_ID>
$P -m curunir_fabric.cli watch-register --store /tmp/fabric/store --need <NEED_ID> \
    --source federal-register-feed --target-kind FEED \
    --target-ref https://www.federalregister.gov/api/v1/documents.rss --cadence 3600
$P -m curunir_fabric.cli tick         --store /tmp/fabric/store   # run from cron
$P -m curunir_fabric.cli verify       --store /tmp/fabric/store
```

Live end-to-end demonstration (real network, three separate processes):

```bash
$P -m curunir_fabric.demo --root /tmp/fabric-demo --phase 1   # plan/acquire/pivot/coverage
$P -m curunir_fabric.demo --root /tmp/fabric-demo --phase 2   # watch baseline (restart)
sleep 70
$P -m curunir_fabric.demo --root /tmp/fabric-demo --phase 3   # change detection, alert, replay
```

## Adding a source

1. Implement a `SourceConnector` subclass (one acquisition shape, injectable
   transport, typed `NativeResult`s) or reuse an existing connector.
2. Register a `SourceDescriptor` (identity/policy vocabulary) plus a
   `CapabilityProfile` (operations, temporal reach, cadence, pagination,
   mutability, biases, gaps, connector binding) via `registry.register_source`.
3. Route identifier schemes in `planner.IDENTIFIER_ROUTES` /
   `pivots._SCHEME_TO_SOURCE` if the source unlocks a new identifier family.

Unit tests run offline against recorded fixture bytes with injected
transports (`tests/test_fabric_*`); mocks there test connectors and are not
claimed as product capability — the live path is `curunir_fabric.demo`.

## Boundaries

No code in this package writes canonical ARGUS PostgreSQL state. The fabric
imports `argus.source_intelligence` and `curunir_operational`; neither
imports the fabric back.

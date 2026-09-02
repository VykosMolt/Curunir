# Curunír semantic plane — world model and active collection

Tranche: `CURUNIR_V6_WORLD_MODEL_AND_ACTIVE_COLLECTION`.

The OSINT fabric gave Curunír eyes and memory (discover → acquire → preserve
→ watch). This package is the semantic nervous system around that fabric and
the first closed intelligence loop:

```
manifestation → normalized document/record → evidence-addressed observations
→ entity/relation/event world model → semantic claims with lifecycle
→ semantic change detection → affected-object propagation → hypotheses
→ discriminating observations → information requirements
→ ranked collection routes → OSINT fabric execution → new evidence → update
```

## Where things live (one world, no second ontology)

| Concern | Where |
|---|---|
| Acquisition edge (registry, connectors, custody, watches) | `curunir_fabric` |
| World-model **state** (`ObjectVersion`, `RelationshipVersion`, `ActivityRecord`), missions, alerts, association, projections | `curunir_operational` (shared plane) |
| Understanding, proposition ledger, change engine, hypotheses, collection planner | `curunir_semantic` (this package) |
| Byte parsing, normalization derivatives, extractor-provider contract, policy, custody | `argus.source_intelligence` + `argus.prospective.research` |

One `SemanticStore` root (a `FabricStore` superset — itself a
`MissionDataStore` superset) carries the whole loop under a single hash chain
with the inherited export/import/replay guarantees.

| Module | Responsibility |
|---|---|
| `contracts` | anchors, normalized documents, observations, claims + lifecycle, semantic changes, hypotheses, discriminators, routes, review queue |
| `store` | `SemanticStore` + replayed views (current claims, states, hypotheses, queues) |
| `normalize` | manifestation bytes → normalized text/field table (JSON field paths, HTML blocks with charset sniffing, XML element paths, plain text, PDF via `pdftotext`); payload custody |
| `extract` | deterministic per-shape extractors (GLEIF, Wikidata, EDGAR FTS, feeds, labeled prose statements, V5.3 role signals) → anchored observations |
| `worldmodel` | observations → objects/relations/events with EVIDENTIARY provenance; dependence families; claim versioning; cross-scheme association proposals; cross-family conflict handling |
| `changes` | observation-set diffing → classified semantic changes → claim lifecycle + review queue |
| `pipeline` | facade: process new evidence; interpret watch changes into semantic alerts; historical discoveries |
| `hypotheses` | V4-discipline competing hypotheses, event-sourced; discriminating observations |
| `collection` | EIV-ranked collection routes (coverage + dependence aware); fabric execution; human tasks |
| `demo` | live three-phase demonstration over real evidence |

## Load-bearing semantics

- **Exact evidence descent.** Every observation carries `EvidenceAnchor`s —
  manifestation + content hash + field path or normalized text span (with a
  declared mapping status) — and every world-model version carries
  EVIDENTIARY `ProvenanceSummary` with per-observation `EvidenceRef`s.
  Anchors are recoverable from replayed payloads alone: a FIELD anchor names
  the field-table payload its path addresses, a TEXT_SPAN anchor recovers
  exactly its value, and a value that cannot be located exactly anchors at
  document scope with `VALUE_NOT_LOCATED_DOCUMENT_SCOPE` — never a
  fabricated span.
- **Bitemporality.** `recorded_time` is always knowledge time. Valid time
  comes only from source-stated times, or, for HISTORICAL manifestations,
  the archive capture time — it never defaults to retrieval time. Evidence
  recency for claim supersession is ranked on **source-state time** (capture
  time for archives, retrieval time for live fetches), so a 2008 capture
  retrieved today updates 2008 and cannot displace today's state.
  `Projection(valid_at=…)` and `as_of_seq` answer both as-of directions
  (BITEMPORAL_LITE), and the semantic plane's current-object reads use the
  projection's ordering rule, never raw log order.
- **Observation ≠ accepted truth.** Machine integration lands as
  `epistemic_state=EXTRACTED`, `review_state=UNREVIEWED`; requirements close
  only through the human-only mission workflow; models propose through the
  operational inference plane (`producer_kind=MODEL_PROVIDER` observations
  require an `inference_id`).
- **Identity stays reversible — and machine paths never accept it.**
  Deterministic identity exists only within one identifier scheme.
  Cross-scheme equivalence (a Wikidata entity *asserting* a LEI) goes through
  the association engine with auto-acceptance withheld
  (`auto_accept=False`): the outcome is a POSSIBLY_SAME_AS proposal plus an
  `IDENTITY_AMBIGUITY` review item, clusters stay separate until a human
  resolves, and any accepted SAME_AS can later be REVERSED. Nothing merges
  destructively.
- **Dependence is counted, not displayed.** Origin families are per publisher
  (three GLEIF responses = one family) and per site (live retrieval, Wayback
  capture and Wayback CDX enumeration of one site = one family). Claims carry
  `independent_basis_count` over distinct families with an explicit
  non-independence caveat; hypothesis support, collection ranking **and
  discriminator satisfaction** consume it — under `independence_required`,
  evidence from a family already in the basis neither ranks nor satisfies.
  By design, claim-level counts are per source-native subject (a GLEIF claim
  and a Wikidata claim about one real-world entity are distinct claims until
  identity is accepted); cross-scheme independence accrues at the hypothesis
  level through linked claims.
- **Newer is not truer across sources.** Within one origin family, newer
  evidence supersedes (new claim version, history kept). A conflicting value
  from an independent family marks the claim DISPUTED and opens a review
  item; nothing is auto-resolved.
- **Corrections preserve history.** SOURCE_CORRECTION/RETRACTION change
  classes set claim lifecycle states (`CORRECTED`/`RETRACTED`) as new
  records; every prior claim version and state stays in the log.
- **Semantic monitoring.** Watch byte-changes are interpreted over typed
  observations: chrome-only edits are SEMANTICALLY_UNCHANGED; real changes
  identify affected objects/claims and raise alerts that explain the change
  (`'Kari Nordmann' → 'Ola Hansen'`), not a hash.
- **Collection is explained.** Routes carry factor vectors (discriminating
  power, coverage gap, independence gain, latency, cost, historical depth,
  access, reliability) with question-adaptive weights and a prose
  explanation; a same-family route under an independence requirement scores
  zero and says why. Re-planning never regresses a route that already
  executed or became a task. Human-required routes become mission analyst
  tasks and are never machine-completed. When no family can independently
  answer, a `COVERAGE_GAP` review item records the dead end; an executed
  search returning nothing opens `EXPECTED_NOT_OBSERVED` — a typed
  uncertainty signal, never an inferred event.
- **Failures are durable state.** A pipeline failure after normalization
  records an OPEN `PROCESSING_FAILED` review item; the manifestation is
  retried on the next pass and the item resolves on success — a half-written
  manifestation can never be skipped as done. Truncated change
  interpretation is recorded as an `UNRESOLVED_CHANGE` record naming what
  was not interpreted.
- **The chain defends itself.** Stores verify the hash chain at load (fail
  closed on corruption) and appends take a file lock with catch-up, so
  concurrent writers on one root extend a single chain instead of forking
  it.

## Running the loop

```bash
cd curunir
P=.venv/bin/python
# understand all evidence in a fabric store and interpret watch changes
$P - <<'PY'
from curunir_semantic.store import SemanticStore
from curunir_semantic.pipeline import SemanticPipeline
pipe = SemanticPipeline(store=SemanticStore("/path/store"), custody_root="/path/custody")
pipe.process_new_evidence()
pipe.interpret_historical_discoveries()
pipe.process_fabric_changes()   # semantic alerts from watch changes
PY
# live three-phase demonstration (real sources; separate processes = restarts)
$P -m curunir_semantic.demo --root /tmp/sem --phase 1 --evidence /path/to/fabric-demo
$P -m curunir_semantic.demo --root /tmp/sem --phase 2
$P -m curunir_semantic.demo --root /tmp/sem --phase 3
```

## Reused V4/V5 machinery

V5.3 typed-observation capture (role signals), the SI normalization
derivatives and extractor-provider contract, the operational association
engine (extended with a temporal-window parameter), mission workflow,
workflow alerts, and the projection plane. The V4 hypothesis shape and
vocabulary are carried into `HypothesisRecord`; the V5.1 contradiction/
dependence engines remain available for deeper text-level dependence work
(the current origin-family model is deliberately simpler and declared).

## Deferred

Themes/narratives, stakeholder modelling, forecasting/warning, analyst GUI,
media understanding, geolocation, translation execution (translations are
representable as derivative anchors; no provider is wired), and broad
connector expansion.

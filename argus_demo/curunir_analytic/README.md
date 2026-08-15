# Curunír analytical intelligence layer

Tranche: `CURUNIR_V6_ANALYTICAL_INTELLIGENCE`.

The semantic plane gave Curunír an evidence-bound world model with exact
anchors, bitemporality, dependence-counted claims, hypotheses and active
collection. This package adds the higher-order structures those facts form —
as **first-class typed state in the same hash-chained event store**, never as
generated prose beside it:

```
propositions/entities/events/relations (curunir_semantic)
→ themes and issues            (themes.py)
→ narratives and discourse     (narratives.py)
→ stakeholders and influence   (stakeholders.py)
→ impact and exposure          (impact.py)
→ historical analogues         (analogues.py)
→ hypotheses / uncertainty     (reused from curunir_semantic)
→ discriminating collection    (collect.py → existing EIV planner)
→ new evidence → updated analysis (propagate.py)
```

| Module | Responsibility |
|---|---|
| `contracts` | frozen validated records: themes, narratives + variants + propagation edges, stakeholder assessments + positions, influence assertions, objectives, assumptions, impact paths + edges, response options, episodes, analogues, transitions |
| `store` | `AnalyticStore(SemanticStore)`: analytical event types + replayed views (one chain, one root) |
| `basis` | the one implementation of claim → evidence arithmetic and full descent |
| `substrate` | context, idempotent transitions, reverse-dependency index, model-candidate boundary |
| `themes` | theme lifecycle, membership, merge/split with lineage, deterministic discovery |
| `narratives` | proposition families, typed variants, propagation vs independence, earliest-observed origin |
| `stakeholders` | contextual assessments, position/interest discipline, typed influence, deterministic discovery |
| `impact` | objectives, assumptions, connected typed paths, uncertainty propagation, response options |
| `analogues` | evidence-bound episodes, structural retrieval, transfer risks |
| `propagate` | semantic change → affected analytics → transitions/alerts, incrementally |
| `collect` | analytical uncertainty → discriminators → mission requirements (existing machinery) |
| `providers` | attributed model-assist boundary; honestly UNAVAILABLE when unconfigured |
| `explain` | eight-section structured explanation for any analytical object |
| `cli` | inspection commands (`themes`, `explain KIND ID`, `dependents --claim`, …) |
| `demo` | five-phase live exercise over real public evidence |

## What each object IS

**A theme** is an evidence-backed recurring/emerging issue: supporting and
contradicting claim membership, entities, events, source-family basis,
temporal interval, status and lineage — all inspectable. A theme without a
supporting claim cannot be constructed; a model-generated title is a
proposal, not a theme.

**A narrative** is a proposition family being propagated. Its two axes are
never conflated: `propagation_reach` (manifestations) versus
`independent_origin_count` (origin families). Variants stay typed
(PARAPHRASE / FRAMING_SHIFT / ATTRIBUTION_SHIFT / … / COUNTER_NARRATIVE);
verbatim identity is derivable, everything else is someone's recorded
judgment. Origin is at strongest `EARLIEST_OBSERVED_KNOWN` — never "proven".

**A stakeholder assessment** is contextual (mission/theme/issue/objective/
event) and temporal — never a permanent global label. `PUBLIC_POSITION`
(claim-backed) and `INFERRED_INTEREST` (structurally barred from OBSERVED
authority) are different record kinds; `FORMAL_AUTHORITY_OVER` and
`LIKELY_INFLUENCES` are different influence kinds; position change is
supersession with both states retained.

**An impact path** is a connected chain of typed, evidence-bearing edges
terminating at its mission objective. Edge kinds carry semantics
(EVENT_EFFECT / DEPENDENCY / TYPED_RELATION / ASSUMPTION_LINK / INFERENCE);
`DIRECT` and `SECOND_ORDER` effects stay distinguishable per edge; a bare
"A→B, 0.8" is unconstructible.

**A historical analogue** compares a situation to an evidence-bound episode
on explicit structural dimensions, exposes matches AND mismatches AND
transfer risks, and has no forecast field.

## Authority / status law

Every analytical assertion carries one of:

```
OBSERVED > DERIVED > ANALYST_ASSESSMENT > SUPPORTED_INFERENCE
        > MODEL_PROPOSAL > CONTESTED > UNRESOLVED
```

Machine output never outranks recorded human judgment. The combinations
that would launder one level into another are rejected at record
construction (an inferred interest can never be OBSERVED; a paraphrase
judgment can never be DERIVED; `LIKELY_INFLUENCES` can never be observed
fact; an analogy is never an observation; MODEL provenance can never wear
OBSERVED, DERIVED or ANALYST_ASSESSMENT). A path's authority is its
**weakest** edge — validated, not trusted: uncertainty propagates, it does
not wash out. An OBSERVED public position additionally requires evidence
structurally attributable to the assessed entity (its own identity, or a
site it controls per latest ACTIVE OPERATES/OWNS/PUBLISHED_BY relations and
site-root references) beyond any inferred interest's claim set.

## Evidence descent

Every object's basis descends programmatically:

```
analytical object → BasisSummary.supporting_claim_ids
→ SemanticClaim → observation_ids → EvidenceAnchor (field path / text span)
→ manifestation → source
```

`basis.describe_descent(store, claim_id)` returns the full chain;
`explain.explain_object(store, kind, id)` gives the eight-section view
(WHAT / WHY / AGAINST / SOURCE_BASIS / TEMPORAL / INFERENCES / UNCERTAINTY /
MISSION_EFFECT).

## Source dependence

`BasisSummary` computes reach and independence from the semantic plane's
origin-family engine: fifty derivative manifestations of one origin are
reach 50, independence 1, and the note says so. Theme status, narrative
analysis, collection ranking and satisfaction all consume families, never
raw counts. Distinct families are *non-identical*, not proven independent —
the caveat travels with every basis.

## Temporal history

Valid time comes from evidence (a 1996 capture supports 1996); knowledge
time is the append. Analytical objects version forward — membership changes,
position supersessions, status transitions and merge/split lineage are new
versions plus typed `AnalyticalTransition` records; nothing is overwritten.
`store.analytic_versions(kind, id)` and `store.transitions_for(id)` replay
the full history.

## Change propagation

`propagate.propagate_semantic_changes(ctx)` consumes the semantic change
ledger, uses `substrate.DependencyIndex` (claim/object/relation/event/
assumption → dependent analytical objects, transitively) to refresh exactly
what a change touches, questions assumptions whose support degraded, exposes
objectives whose dependencies moved, refreshes touched hypotheses through
the existing engine, and raises evidence-bound `analytic-change` alerts that
state the evidence, the analytical effect and the dependent mission state.
Everything is idempotent by cause: re-running after a crash completes
propagation instead of duplicating it.

## Analytics drive collection

`collect.analytic_collection_needs(store)` recognizes four uncertainty
patterns (single-family theme; unresolved narrative origin; inferred
interest without a primary statement; weak impact edge) and
`open_analytic_requirements` turns them into discriminators + mission
information requirements through the EXISTING active-collection machinery —
the EIV planner's dependence arithmetic then scores a same-family route at
zero, visibly.

## Model assistance

`providers.AnalyticalAssist` is the only gate: each invocation is an
operational `InferenceRecord`, output lands as an
`ANALYTICAL_OBJECT_CANDIDATE` proposal, and only a recorded HUMAN act
(`substrate.resolve_candidate`) can accept it. Materialization is then
BOUND to that acceptance (`substrate.require_accepted_candidate`): the
target kind must match, every field shared with the accepted content must
be equal, and the acceptance is consumed by the first object materialized
from it — one acceptance is never a bearer token. The materialized object
keeps `provenance_kind=MODEL` plus the inference identity forever and lands
as SUPPORTED_INFERENCE. With no provider configured the capability reports
`ANALYTICAL_PROVIDER_UNAVAILABLE`; deterministic candidates and analyst
acts are the always-working path. There is no keyword-rule fake NLP
anywhere.

## Replay

The layer is pure event-log state: a fresh `AnalyticStore(root)` (or
`export_to`/`import_from`) reconstructs every view, history and explanation
with zero network or provider calls — `tests/test_analytic_replay.py` loads
with `socket` disabled to prove it.

## Running

```bash
cd argus_demo
# five-phase live exercise (real public sources; separate processes = restarts)
.venv/bin/python -m curunir_analytic.demo --root /tmp/analytic-demo --phase 1
.venv/bin/python -m curunir_analytic.demo --root /tmp/analytic-demo --phase 2 --operator you
.venv/bin/python -m curunir_analytic.demo --root /tmp/analytic-demo --phase 3 --operator you
.venv/bin/python -m curunir_analytic.demo --root /tmp/analytic-demo --phase 5   # analogue
.venv/bin/python -m curunir_analytic.demo --root /tmp/analytic-demo --phase 4   # replay
# inspection
.venv/bin/python -m curunir_analytic.cli --root /tmp/analytic-demo themes
.venv/bin/python -m curunir_analytic.cli --root /tmp/analytic-demo explain analytic_theme THEME_ID --text
# tests
.venv/bin/python -m pytest tests/ -k analytic
```

## Deferred

Forecasting/calibration/strategic warning (V6.5), the analyst workbench GUI
(V6.6), report/dossier generation (structured projections exist; rendering
does not), and mission-package impact taxonomies (core carries only the
propagation machinery).

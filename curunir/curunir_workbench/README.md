# Curunír analyst workbench (`curunir_workbench`)

**Tranche:** `CURUNIR_V6_ANALYST_WORKBENCH_AND_COLLABORATIVE_OPERATIONS`

The operator surface over one Curunír mission store. A human analyst operates
the full intelligence loop — see what matters, inspect why, descend to exact
evidence, challenge the analysis, collect more, collaborate, make an
attributable judgment, construct an evidence-bound dossier — from a normal
browser, without research scripts or raw JSON.

## Architecture

```
one mission root
├── store/      WorkbenchStore < AnalyticStore < SemanticStore < FabricStore
│               < MissionDataStore — ONE append-only hash-chained event log
│               carrying every plane (workflow, fabric acquisition, semantic
│               understanding, analytics, workbench collaboration/reports)
├── custody/    content-addressed source bytes (SourceCustodyStore)
└── actors.json bearer-token → actor/access registry (deployment config,
                never mission truth, never served)

            ┌──────────── server.py (FastAPI) ────────────┐
            │   bearer token → AccessContext (auth.py)    │
   queries  │  MissionProjection: filtered BEFORE          │  commands
   (GET)    │  serialization; hidden = nonexistent         │  (POST)
            │  views.py/provenance.py/search.py/reports.py │  commands.py →
            │                                              │  canonical plane
            └────────────── static/ (no-build ES modules) ─┘  functions only
```

* **Projection boundary** (`projections.py`): `MissionProjection(store, context)`
  composes the operational world view with every fabric/semantic/analytic/
  workbench record family. A record the context cannot view appears in no
  list, count, graph, search hit, timeline, chain or export; visible records'
  references to hidden state are redacted (`REDACTED`); unknown and forbidden
  are indistinguishable (both 404). Source *descriptors* (the registry of
  where Curunír can look — not what it found) are catalog metadata visible to
  any authenticated context; every acquired/derived record is marking-gated.
* **Command boundary** (`commands.py`): the browser names a command; the
  server resolves the actor from authentication and calls the same store
  functions the rest of Curunír uses. Human-only guards (task closure, model
  candidate acceptance, forecast authorship/movement, review disposition,
  report approval) live in the canonical layers and are surfaced, never
  bypassed. Versioned families carry `expected_version`; a stale write is a
  409 with the current version — never a silent overwrite (the store's strict
  next-version enforcement backstops the check inside the append lock).
* **No second ontology**: the frontend renders truth produced by the server.
  Object identities are canonical ids everywhere; selecting an object in any
  view pivots into the same dossier.

## Major views

Mission (overview/COP, investigation, search, activity) · World (entities,
events, valid/knowledge timeline, typed relationship graph, evidence-backed
map, evidence viewer with exact span/field anchors, source registry +
independence) · Analysis (themes, narratives with variant/propagation vs
independence, stakeholders with position≠interest and authority≠influence
labeling, impact paths with per-edge basis, hypothesis comparison matrix
projected from claim links) · Forecasting (authored probability history,
indicators with coverage semantics, warning center with named-rule tier
explanation) · Operations (EIV-ranked collection routes with launch/assign,
coverage matrix where NOT_SEARCHED ≠ absence, watches, tasks, unified review
queue) · Output (annotations/dissent, report/dossier engine).

## V6.8 pilot surface

`tools/curunir_v68.py` wraps this server with `/v68/pilot/*` control routes and
an audit middleware. The sign-in form probes those routes (a pilot server
answers 401, the plain workbench 404) and, when they are there, offers a
participant role: with one chosen, the session starts before the first
authenticated read, so every recorded action falls inside a session. The SPA
then shows a `PILOT` nav group and lands on it: session end, the
operator-correction event and the frozen mission brief are controls on
`/pilot` rather than `curl` invocations, and ending a session makes no further
read. Forecast authorship, dossier creation at a compartment and role floor,
claim citation, review dispositions, task closure and approval notes are
likewise ordinary form controls, so the whole protocol is drivable — and
testable — in a browser (`tests/test_v68_pilot_journeys.py`).

`/api/session` reports the actor's `compartments` and `releasability` so those
forms can offer only markings the actor holds. It is display, not authority:
every marking decision still happens server-side at the command boundary.

Requests carry `X-Curunir-Client: workbench-ui`; the harness records the
value each request claimed on its `HTTP_ACTION` and counts mutating requests
by it. The header is unauthenticated: a script that sets it is counted as the
UI, so the count is descriptive evidence of how the operator worked, not proof.
The task-closing, route-assignment and saved-view notes are required by the UI
only; the operator-correction note is required by the harness as well.

## Provenance descent

First-class both ways, access-filtered at every hop:

```
warning → objective → impact path → forecast (authored judgment)
        → assumptions → claims → observations → EXACT anchor
        (JSON field path / text offsets) → manifestation (custody sha256)
        → source
```

and upward: source → manifestations → observations → claims → dependent
analytical objects → warnings. A link into hidden state terminates in an
explicit "not accessible" node. Evidence payload bytes are served only when
their sha256 matches the recorded custody hash.

## Collaboration

Annotations (`NOTE`/`QUESTION`/`DISSENT`/`CORRECTION_SUGGESTION`) bind to
canonical records the author can see; dissent never overwrites the assessment
it disagrees with and blocks plain report approval until resolved or carried
visibly as `APPROVED_WITH_DISSENT`. Hypothesis assessments re-append with an
attributable history entry. All workflow transitions (assign/claim/complete,
review dispositions) are recorded human acts through `MissionWorkflow`.

## Report / dossier engine (`reports.py`)

A report is a versioned structured projection, not generated prose. Every
sentence is `SUPPORTED` (resolvable observation-backed basis),
`EXPLICITLY_INFERENTIAL` (inference note + assumptions exposed) or
`UNRESOLVED` (reason stated). Validation runs against the **approving
actor's own authorized projection** and rejects: missing/unresolvable basis,
inference laundered as observation, contested/stale basis rendered settled,
historical state rendered current, independence asserted over a single origin
family, and quoted probabilities that differ from the authored forecast
record. Approval is human-only, records the validation sha256 + state token,
and pins an immutable version; revision is a new version. Role projections
(ANALYST / EXECUTIVE / SOURCE_LEGAL / OPERATOR) derive from the one record.
Exports: JSON package with expanded basis lineage, deterministic HTML.

## Access model (V6.6 scope, stated honestly)

Bearer tokens in `actors.json` map to actor identity + roles/compartments/
releasability (`curunir_operational.access` fail-closed model). This gives
every action an attributable actor and every projection a server-side filter;
it is NOT production PKI/credential rotation — that is the V6.7 security
tranche. "Signatures" on dispositions are the authenticated actor identity
recorded in the hash-chained log.

### Marking derivation (how a new/derived record is classified)

Read-side filtering (the projection scrub) is only half the model; the other
half is that a record must be *written* at a marking no less restrictive than
anything it is about. Two rules, applied at the command boundary:

* **New record** → `_reference_marking`: its marking is the high-water-mark
  (`access.most_restrictive`) of the author's declared marking and the
  markings of every reference it cites. A forecast citing a compartmented
  assumption, a report whose sentence rests on a compartmented claim, a watch
  on a compartmented object, a requirement/task naming compartmented affected
  state — each is itself compartmented. The author must be cleared for the
  result (floor check), else the write is refused.
* **Re-append / fold / transition on an existing subject** →
  `_guard_reference_floor`: the subject keeps its own marking (a re-append
  never re-classifies), so a cited reference more restricted than that marking
  is refused rather than silently under-classified.

Records *derived* from a subject inherit that subject's marking through the
same context: `ctx.analytic(marking)` / `ctx.pipeline(marking)` stamp every
companion write (transitions, warnings, review items, discriminator updates,
acquired evidence) with the subject's marking. The semantic pipeline derives
each manifestation's understanding from that manifestation's own marking, and
the cross-scheme identity sweep marks an equivalence proposal (and its review
item) with the join of both endpoints' markings.

`most_restrictive` fails closed rather than emit a marking that would
downgrade an input (org-locked/releasability-collapse and cross-authority
joins raise). The invariant this whole layer defends —
*after any command touching a compartmented subject, no newly-appended record
of any family is visible to an uncleared context* — is asserted directly by
`tests/test_workbench_review_repairs_4.py::test_no_command_declassifies_a_special_subject`,
so a regression in any single derivation site fails a test rather than leaking.

This model was hardened across six adversarial review rounds. What remains is
V6.7-scoped and documented as such: an adversarial insider pasting a guessable
compartmented id into free text (a bounded existence oracle for mnemonic ids;
production ids are digest-shaped and symmetric), multi-authority /
split-releasability MLS join algebra, and provider/scheduler paths not
reachable from the workbench command surface.

## Restart / replay

All state derives from the event log; a restarted process reconstructs
identical authorized projections, and `export_to`/`import_from` (hash-chain
verified, tamper-refusing) plus the custody directory reproduce the mission
elsewhere without network or model providers.

## Running

```bash
cd curunir && source .venv/bin/activate
CURUNIR_MISSION_ROOT=missions/apple_workbench_v66 \
CURUNIR_ACTORS=missions/apple_workbench_v66/actors.json \
uvicorn --factory curunir_workbench.server:app --port 8100
# open http://127.0.0.1:8100 and sign in with a token from actors.json
```

## Integrated live demonstration

```bash
# 1. populate a real mission root (live GLEIF/Wikidata/SEC/Wayback):
python -m curunir_analytic.demo --root missions/apple_workbench_v66 --phase 1
python -m curunir_analytic.demo --root missions/apple_workbench_v66 --phase 2 --operator jan
python -m curunir_analytic.demo --root missions/apple_workbench_v66 --phase 3 --operator jan
python -m curunir_analytic.demo --root missions/apple_workbench_v66 --phase 6 --operator jan
# 2. run the full operator exercise through the HTTP boundary
#    (overview → descent → hypothesis → LIVE route launch → annotation →
#     restricted second analyst → review disposition → dossier validation
#     rejection → repair → approval → restart → replay):
python -m curunir_workbench.demo_mission --root missions/apple_workbench_v66
```

## Tests

```bash
python -m pytest tests/test_workbench_projections.py tests/test_workbench_reports.py \
  tests/test_workbench_commands.py tests/test_workbench_server.py \
  tests/test_workbench_replay.py tests/test_workbench_browser.py
```

`test_workbench_browser.py` drives real Chromium (Playwright) against a real
server over a real seeded store — sign-in, COP, warning→evidence descent,
hypothesis assessment, annotation, the full dossier flow, restricted-analyst
filtering, timeline axes.

`tests/test_v68_pilot_journeys.py` drives the V6.8 pilot protocol itself
through the UI alone: M2 (correction → review disposition → dossier → signed
approval), M3 (operational picture → task → compartmented dossier → approval
with dissent → public access check), and an M1 analogue on the seeded demo
mission that also proves the pilot nav stays hidden without the `/v68` routes.

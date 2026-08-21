# Curunír V6.8 human-pilot protocol

Authority: `CURUNIR_V6_8_QUALIFICATION.json` and
`CURUNIR_V6_8_MISSIONS.json`. This protocol operates the existing workbench;
it is not a new UI or product plane.

## Custody and participants

Use one new campaign root prepared by the repaired executable. Roots prepared
by the reviewed `5bb32e9` executable are retained failure evidence and are not
qualification inputs. Never reuse, edit, or delete a mission root after
preparation. `actors.json` contains bearer credentials and stays local; it is
mode `0600` and is deliberately excluded from final packages.

The primary operator and approver must be different real people for every
mission, and every approval uses the existing signed browser action. Automated
requests, a service identity, or a second token used by the same person do not
satisfy the contract. The public-access check in M3 may be performed by either
person after signing out, using the separately constrained read-only public
actor. The harness proves attributable identities, ordered session activity,
and genuine signatures; it does not prove biological presence or that two
tokens were controlled by different people.

Each server is bound to loopback. Before starting work, read the frozen mission
brief returned by `/v68/pilot/brief`. The harness records actor, path, action
class, response status, store transition, and elapsed server time. It never
records bearer tokens, signing keys, request bodies, keystrokes, or unrelated
browsing.

## Common launch and session procedure

From `argus_demo` in the V6.8 executable checkout:

```bash
export PYTHONPATH="$PWD:/home/moloch/Saulot/argus_demo"
export V68_PYTHON=/home/moloch/Saulot/argus_demo/.venv/bin/python
export CAMPAIGN_ROOT=/home/moloch/Saulot/curunir_v68_runs/V68_TERMINAL_002
$V68_PYTHON -m tools.curunir_v68 prepare-all --campaign-root "$CAMPAIGN_ROOT"
```

`prepare-all` refuses a dirty executable checkout and records the exact git SHA
and external-kernel identity. It performs baseline live public acquisition for
M1. If an external source
fails, retain `preparation_failure.json`; do not relabel the root ready.

For one mission, choose an unused loopback port and start:

```bash
$V68_PYTHON -m tools.curunir_v68 serve \
  --root "$CAMPAIGN_ROOT/MISSION_ID" --port PORT
```

In a second terminal, extract only the actor token you are about to use:

```bash
export TOKEN="$(jq -r '.actors[] | select(.actor_id == "v68-primary-operator") | .token' \
  "$CAMPAIGN_ROOT/MISSION_ID/actors.json")"
curl -fsS -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"participant_role":"PRIMARY_OPERATOR","note":"begin frozen mission"}' \
  http://127.0.0.1:PORT/v68/pilot/start
curl -fsS -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:PORT/v68/pilot/brief | jq .
```

Open `http://127.0.0.1:PORT/`, enter the same token, and use the shipped
workbench. At the end, record the matching end event:

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"participant_role":"PRIMARY_OPERATOR","note":"mission work complete"}' \
  http://127.0.0.1:PORT/v68/pilot/end
```

Use `participant_role=APPROVER` with actor `v68-human-approver`. Use
`participant_role=PUBLIC_ACCESS_CHECK` with actor `v68-public-observer`.
Never share one browser signing profile between the two human actors.

## M1 — corporate-registry continuous capstone

Primary operator:

1. Inspect Overview, sources, current claims, at least two exact evidence
   payloads, and provenance descent. Note coverage gaps and dependence.
2. In Investigation, record at least two genuinely competing hypotheses about
   registry continuity. Link exact claims and record the initial assessment.
3. Inspect Themes, Narratives, Stakeholders, Objectives, and Impact. Do not
   promote analytical objects to observations.
4. Before additional collection, author a falsifiable forecast. Initial
   authorship uses the existing HTTP command. Substitute a visible claim id
   from `/v68/pilot/brief`:

   ```bash
   curl -fsS -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{
       "question":"Will the next preserved GLEIF retrieval within 30 days report a different registration status for the capstone LEI?",
       "outcome_semantics":"TRUE only if a later hash-preserved GLEIF manifestation yields a different registration_status value for the same LEI.",
       "horizon_time":"2026-09-20T23:59:59+00:00",
       "probability":0.10,
       "probability_basis":"Operator judgment from the preserved baseline; this is not an observed frequency.",
       "proposition_refs":[["claim","CLAIM_ID"]],
       "resolution":{"kind":"HUMAN_JUDGMENT","criteria":"Resolve against the later preserved GLEIF status claim at or after the horizon."},
       "domain":"CORPORATE_REGISTRY"
     }' http://127.0.0.1:PORT/api/commands/forecasts
   ```

   The value `0.10` is an operator input, not a qualification threshold. Change
   it if that is not the operator's judgment.
5. In Collection, launch a viable GLEIF route. A real new manifestation must
   arrive. A retained external-source failure is data but does not satisfy the
   capstone update gate.
6. Inspect the new payload and resulting state. Reassess at least one existing
   hypothesis after that arrival. Open the existing forecast and record a new
   probability version with the evidence update and change reason; do not move
   it merely to satisfy the harness. Project the revised forecast onto the
   frozen objective using the objective id from the brief.
7. Create a dossier. Every `SUPPORTED` factual sentence must contain the exact
   cited claim statement or its exact asserted value and cite that visible
   claim. This deliberately conservative content binding prevents an unrelated
   true record from laundering false prose. Inspect the claim's exact evidence
   descent. Inference must be `EXPLICITLY_INFERENTIAL` with a note; gaps must be
   `UNRESOLVED`.
   Submit it for review.
8. Record the primary session end.

The distinct approver then starts an `APPROVER` session in a separate browser
profile, independently opens the cited evidence and validation panel, and uses
**Approve (validated, human act)**. The browser must report Ed25519 availability
and retain a genuine signed action. The approver then records session end.

## M2 — regulatory correction

Primary operator:

1. Inspect all four frozen manifestations: structured v1, English v1,
   Croatian derivative, and correction v2. Verify their identities and time
   ordering in the workbench.
2. Record two competing hypotheses for the affected facility. Treat the
   Croatian page as a dependent historical derivative of English v1, never as
   independent or current N-4 corroboration after correction v2.
3. Inspect the semantic changes and identify the N-4 to N-9 correction.
4. Record the human rework event when revising the initial reading:

   ```bash
   curl -fsS -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"participant_role":"PRIMARY_OPERATOR","note":"revised initial N-4 reading after exact v2 correction review"}' \
     http://127.0.0.1:PORT/v68/pilot/correction
   ```

5. Reassess the hypotheses. Produce and submit a report whose `SUPPORTED`
   sentences use the exact claim statement or asserted value, explicitly binds
   current N-9 to correction v2, labels N-4 historical, and distinguishes
   observation, inference, and unknowns.
6. End the primary session. The distinct approver reviews exact evidence,
   approves it using **Approve (validated, human act)** with Ed25519 available,
   and records an `APPROVER` start/end pair.

## M3 — relief collaboration and access control

Primary cleared operator:

1. Inspect operational objects/events, task state, public route evidence, the
   SPECIAL engineering evidence, correction history, and generated warning or
   recommendation.
2. Add an attributable annotation and advance the assigned task through the
   existing workflow. Create the report at the SPECIAL floor before editing;
   a public draft correctly cannot be raised later by citing restricted basis:

   ```bash
   curl -fsS -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{
       "title":"V6.8 M3 relief-routing disposition",
       "question":"What disposition is supported for RELIEF-101?",
       "compartments":["SPECIAL"],
       "sections":[{"kind":"key_judgments","title":"Key judgments","sentences":[]}]
     }' http://127.0.0.1:PORT/api/commands/reports
   ```

   Open that report in the workbench. Produce a compartmented result whose
   supported claims cite only visible basis and whose limitations remain
   unresolved.
3. Submit and end the primary session.

The second cleared human starts an `APPROVER` session, independently inspects
the restricted basis, adds a genuine `DISSENT` annotation to the report or a
report sentence, and approves only if satisfied. Open dissent must be carried
visibly as `APPROVED_WITH_DISSENT` or resolved honestly. Use browser signing.

Finally sign out, use the `v68-public-observer` token, record a
`PUBLIC_ACCESS_CHECK` start, inspect Overview, Evidence, Search, Graph, and
Reports, and confirm the SPECIAL engineering record and its identifier are not
enumerable in full or truncated form. The public actor is read-only: any
non-session mutation is a required refusal. Do not obtain the restricted id
from the cleared session merely to probe it. Record the public access-check end.

## Gate and freeze

After each mission, stop its server and run the non-mutating check:

```bash
$V68_PYTHON -m tools.curunir_v68 check --root "$CAMPAIGN_ROOT/MISSION_ID"
```

`NOT_ACHIEVED` is a result, not permission to waive a step. Repair only class A
or B defects, rerun the affected mission from a new root where evidence custody
would otherwise be ambiguous, and preserve failed roots.

When `check` passes, freeze exactly once:

```bash
$V68_PYTHON -m tools.curunir_v68 finalize --root "$CAMPAIGN_ROOT/MISSION_ID"
$V68_PYTHON -m tools.curunir_v68 verify-package --root "$CAMPAIGN_ROOT/MISSION_ID"
```

Do not mutate the mission store after finalization. The terminal validator also
requires a fresh accepted-residual V6.7 report and refuses missing coverage or
manual waiver.

From a clean exact executable checkout, the terminal reproduction command is:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/curunir-v68-playwright \
  $V68_PYTHON tools/validate_v67.py \
  --kernel /home/moloch/Saulot/argus_demo/argus \
  --report /tmp/curunir-v68-v67-current.json && \
$V68_PYTHON -m tools.curunir_v68 terminal \
  --campaign-root "$CAMPAIGN_ROOT" \
  --v67-report /tmp/curunir-v68-v67-current.json \
  --output /tmp/curunir-v68-terminal.json
```

The V6.7 report must name the same executable SHA as the checkout. Its status
may be clean `PASS`/`PASSED` or `PASS_WITH_ACCEPTED_BASELINE_RESIDUALS`; the
terminal validator independently checks the exact residual subset, outcome
kinds, test-collection bounds, clean reconstruction, and kernel pin. For each
mission it also re-runs faithfulness, denied-network replay, assessment,
measurement, and coverage from the live immutable root and requires exact
agreement with the packaged projections.

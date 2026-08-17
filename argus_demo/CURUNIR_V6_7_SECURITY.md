# Curunír V6.7 — Security & Resilience Reference

Concise product-security reference for the V6.7 hardening tranche. It describes
the invariants the code enforces and the bounded limitations it does not, so an
operator or reviewer can reason about the trust chain without reading the diff.

The tranche hardened the **tracked** integrated product (`curunir_fabric`,
`curunir_semantic`, `curunir_analytic`, `curunir_workbench`). The ARGUS kernel
(`argus/`), `requirements.txt`, `conftest.py`, `docker-compose.yml`,
`schema.sql`, and `missions/` are deliberately **untracked** (kernel shipped as
separate snapshots), so every security control here lives in tracked
`curunir_*` code.

## Threat boundary

The trust chain is: external hostile evidence → acquisition boundary → immutable
custody → semantic interpretation → analytical state → (future) provider/model
proposals → human authority → forecasts/warnings → decision output. V6.7
strengthens the transitions where untrusted content, background execution, or
low-marked context could otherwise manufacture disclosure or authority it does
not hold.

## Information-flow marking (the load-bearing invariant)

A marking is an **information-flow property**, not a field copied at
construction. The rule, enforced product-wide:

> A derived or re-appended record can never be viewable by a context that could
> not view the material state it embeds — a claim's value, a restricted count or
> existence, a transition detail.

Mechanisms:

- `curunir_operational/access.py::most_restrictive` — the high-water-mark join;
  raises (fail-loud) when no single marking can safely express the join
  (cross-authority / disjoint releasability). Interactive command guards call
  this directly so they refuse loudly.
- `curunir_operational/access.py::inherited_marking(base, refs)` — the **total**
  derive-a-marking primitive for background/derived records: it raises to the
  join, and where no single Marking can express the join (cross-authority, or
  orthogonal releasability channels) it **seals to deny-all** — a marking
  carrying the reserved `curunir:unjoinable-seal` compartment that no context
  holds, so the derived record is withheld from every projection (auditable in
  the log, pending explicit human reclassification) rather than aborting the
  pass. Crucially a satisfiable seal would *under-classify* (a single Marking
  cannot express "org X AND release REL_A" across orthogonal axes), so the only
  safe seal is one viewable by no one. Verified by the never-write-down property
  test across every axis.
- Re-appends floor on the object's **own** marking and raise only by the
  **material** references they embed — never by the execution context — so a
  coalition object refreshed in a lower background pass keeps its own
  classification instead of spuriously colliding with the context marking.
- `curunir_analytic/substrate.py::append_version` — single chokepoint: a new
  analytic object version is floored on its prior version's marking, so no
  engine can declassify a compartmented object by re-appending it in a lower
  context.
- `record_transition(..., reference_markings=)` — a transition inherits the
  markings of the claims/evidence its detail summarizes.

This is applied on the **background/non-workbench** paths specifically:
`process_fabric_changes` → semantic change records, claim-state and review
items, and alerts; analytic propagation → theme/narrative/stakeholder/impact
refreshers, their transitions, and analytic/forecast-plane alerts; and forecast
resolution (see below). Aggregate/count signals ("N sources", "N degraded")
inherit their basis claims' markings, so a count over a compartmented claim is
not readable at a lower marking.

Marking is not maximised blindly: independently public-supported state stays
public; only records that *materially derive from* more restricted state are
raised. Access-relative recomputation of a mixed-marking object's projection
from an uncleared actor's visible basis (rather than raising/hiding the whole
object) is a deliberate future refinement, noted under Limitations.

### Closed residual — forecast-resolution value leak

The named V6.6 residual: `try_machine_resolution` embedded a resolving claim's
literal value into a forecast's resolution reason, landing in a
`ctx.marking`-stamped version and transition. Now the resolved version and its
transition inherit the resolving evidence's marking (`forecasts.py::_ref_markings`),
so a restricted claim value never surfaces in a record a lower context can read.
Regression lock: `tests/test_forecast_resolution_marking_leak.py`.

## Network egress / SSRF boundary

The acquisition transport fetches attacker-influenced URLs and follows
redirects. `curunir_fabric/net_guard.py` is the tracked guard, installed as the
default transport (`connectors/base.py::SAFE_DEFAULT_TRANSPORT`):

- scheme allowlist (`http`/`https`); everything else refused;
- the host is resolved and refused if **any** address is loopback / private /
  link-local (covers the `169.254.169.254` metadata address and IPv6 ULA) /
  multicast / reserved (covers NAT64, 6to4, v4-compat) / unspecified; IPv4-mapped
  IPv6 is judged on the mapped v4;
- redirect hops and the final URL are re-checked; bytes reached through a
  forbidden hop are never returned, so nothing internal is ingested.

Numeric-encoding, IPv6, userinfo, trailing-dot and case bypasses were tested and
blocked (`tests/test_fabric_ssrf_guard.py`). **Bounded limitations:** the
underlying transport follows redirects internally, so a redirect's GET fires
before the post-hoc ingestion refusal (full prevention needs hop interception in
the untracked kernel transport); and there is a check-then-connect DNS window
(pinning would need transport-level control).

## Provider governance / egress

There is currently **no live model provider** — `AnalyticalAssist.propose` is
the one designed gate but is unwired (no callers). Its egress control is in
place for when a provider is wired: it derives the effective input marking from
the actual referenced objects and **refuses before the provider call** when the
input exceeds the provider's declared `allowed_input_marking`; an ungoverned
provider (no policy) receives public-releasable evidence only. Provider output
still enters solely as an untrusted `ANALYTICAL_OBJECT_CANDIDATE` bound to its
inference record, awaiting human acceptance. Lock:
`tests/test_provider_egress_gate.py`.

## Cryptographic actor identity & signed actions

Real Ed25519 public-key identity (`curunir_identity`, on the `cryptography`
library — `cryptography==44.0.0`, pinned). An actor holds a private key; the
mission log holds only enrolled public keys and their lifecycle. This replaces
the bearer-token trust model for load-bearing acts (the bearer path remains for
read views).

- **Key registry** (`registry.py`) — enroll / rotate / revoke / retire are
  versioned events in the mission log, so a signature stays verifiable against
  the exact window the key was valid; revocation stops future use without
  voiding past signatures, and a key marked *compromised* is distrusted
  retroactively.
- **Authentication** (`sessions.py`) — challenge-response: the actor signs a
  server-issued, single-use, short-lived nonce; the server verifies against the
  enrolled public key and issues a short-lived session, re-checked against live
  key status on every use (so a mid-session revocation stops further use). The
  client only proves key possession; it never asserts its own identity or roles.
- **Authorization is separate** — an authenticated actor's roles/compartments
  come from current registry state (`auth.context_for_actor`), not the key and
  not the client. A valid key never implies a permission.
- **Signed actions** (`actions.py`) — a load-bearing act is signed over a
  canonical binding of actor + kind, action type, the target and its exact
  version token, the mission, a single-use nonce, timestamp, and the material
  command. Verification refuses an expired/foreign session, a wrong actor or
  mission, a replayed nonce, a stale target version, a key not valid at signing
  time, or a signature that does not verify — before the command runs.
- **Replay** (`replay.py`) — every recorded signed action re-verifies against
  the key valid when it signed, distinguishing genuine / tampered / revoked-at-
  time / unknown-key. The hash chain proves the log was not altered; the
  signature proves authenticity — neither substitutes for the other.
- **Service ≠ human, four-eyes preserved** — service keys are distinct actor
  kinds; a human-only gate (report approval) refuses a service signature.
  Separation of duties is a property of the logical actor, so rotating a key or
  opening a second session never yields self-approval.
- **HTTP surface** — `POST /api/auth/enroll` (bearer-bootstrapped),
  `/api/auth/challenge`, `/api/auth/authenticate`, `GET /api/auth/time`, and a
  signed `/api/commands/reports/{id}/approve-signed` carry the flow over the
  wire.
- **Browser last mile (§2) — delivered.** The real Chromium workbench performs
  its load-bearing act (report approval) through a **non-extractable WebCrypto
  Ed25519 device key**, generated in-page and persisted in IndexedDB: JS can ask
  it to sign but cannot read it, so — unlike the bearer token — the credential
  cannot be exfiltrated for offline or elsewhere use. The browser builds the
  canonical signing bytes itself (`static/js/canonical.js`, proven
  byte-identical to the server serializer over an adversarial corpus by
  `tests/test_workbench_canonical_js_parity.py`) and the server independently
  re-derives and verifies them against the real operation — what-you-see-is-
  what-you-sign, neither side trusting the other's serialization. The bearer
  path remains only as an explicit fallback where in-browser Ed25519 is
  unavailable, and for read projections. **Enrollment trust boundary:** the
  one-time enrolment of a device public key is authorized by the bearer token
  and bound server-side to the bearer-authenticated actor (the request body
  carries no actor id, so a client cannot enrol a key for another actor);
  thereafter each load-bearing act requires the private key. One active signing
  key per actor per device — a new key rotates the previous one, whose past
  signatures stay verifiable. Proven end to end (real browser + live server) in
  `tests/test_workbench_signed_identity_browser.py`: a genuine signed approval
  recorded for replay, four-eyes preserved, the private key non-exportable, and
  the wire refusing real browser-signed transplant / stale-version / wrong-actor
  / tampered-signature attacks and a revoked-key session.

## Secrets

No external-service credentials are read on the active path. The workbench
bearer-token registry lives **outside** the mission store, is never exposed by
the API, is compared in constant time, and is written at mode 0600. Secrets do
not enter event-store records or exports by construction.

## Subprocess / content parsing

The only external binary on the live path is `pdftotext`, invoked with list args
(no shell), a 30s timeout, return-code checking, and now a bounded output size
(`normalize.py`, 25 MB cap + `PDF_TEXT_DERIVATIVE_TRUNCATED_TO_BOUND` warning).
Acquired HTML is parsed to inert text (scripts flagged, never executed); feeds
parse with the stdlib. No acquired content reaches a model today (provider seam
unwired), so there is no live prompt-injection surface; the structural
candidate-binding contract already constrains any future provider output.

## Resource governance & limit-truthfulness (§3)

The acquisition edge fetches attacker-influenced bytes and drives downstream
absence reasoning, so a resource limit, a source-side throttle, or a hostile
body must never (a) run unbounded work or (b) be recorded as evidence.

- **A resource limit is never evidence of absence.** A byte-capped (truncated)
  retrieval did not see the whole source, so it is excluded from absence
  coverage: `forecasts.py::_absence_coverage_satisfied` drops any execution
  flagged `truncated` (now carried on `ExecutionRecord`, and cross-referenced to
  the manifestations for older records), and says so in the coverage
  explanation. A truncated search can no longer resolve a forecast FALSE. Lock:
  `test_analytic_forecasts.py::test_truncated_search_does_not_resolve_false_by_absence`.
- **A source-side error/throttle 200 is a failure, not an empty result set.**
  `EMPTY` (which legitimately feeds absence) now means the connector positively
  parsed a well-formed response with zero matches. A JSON:API `errors` document
  or a body with no `data` (GLEIF), a body with no `hits` container (EDGAR), or
  an empty CDX body (Wayback) classify `FAILED` — excluded from absence. Genuine
  empties still classify `EMPTY`. Locks: `test_fabric_resource_governance.py`.
- **A hostile body cannot abort the whole pass or expand unbounded.** The
  connector call and manifestation build are contained in `executor.py`: an
  exception becomes one truthful `SOURCE_FAILED` record (never absence), so one
  crashing source no longer silently skips every remaining query. The RSS parser
  refuses any feed declaring a DOCTYPE/ENTITY before handing bytes to the
  entity-expanding stdlib parser (billion-laughs / XXE defense, whole-body scan).
  Malformed CDX rows are skipped individually rather than aborting.
- **A poison record cannot loop forever.** A manifestation that fails processing
  is auto-retried at most `MAX_PROCESSING_ATTEMPTS` (5) times — each retry can
  fork a 30 s `pdftotext`, so one unprocessable record would otherwise be an
  unbounded work loop; its OPEN `PROCESSING_FAILED` item still stands for a
  human, and a later success resets the budget. Locks:
  `test_semantic_resource_governance.py`.
- **Fan-out is bounded.** One response's pivot proposals are capped
  (`MAX_PIVOTS_PER_RESPONSE`), so a verbose/hostile response cannot drive an
  unbounded number of store appends.

Existing byte/time bounds are preserved: the 40 MB per-request cap and 30 s
timeout at the transport, the 25 MB `pdftotext` output cap, connector
`limit ≤ 100`, plan `budget_max_requests ≤ 500`, and the ≥ 60 s watch cadence
floor.

## Failure / recovery, backup / restore

The mission store is an append-only hash chain (`verify_chain`) with
content-addressed payloads. `export_to` / `import_from` provide backup and
clean restore into a fresh store; `import_from` verifies the events hash, the
store-meta hash, every payload's content hash, the chain, and the head — a
tampered or truncated backup is refused, never silently reconstructed. Restore
+ replay reconstructs mission state with no network and no provider, and
restricted markings + access control survive the boundary. Locks:
`tests/test_backup_restore_marking.py`.

## Known bounded limitations (not V6.7 defects)

- **Signed-action crash window** (F-06 residual): the command commits, then the
  signed-action record is appended; a crash between the two leaves the act
  applied without its cryptographic non-repudiation record (the command's own
  actor attribution still survives; crash-only, never attacker-triggerable).
  Full closure needs the two appends to be one atomic transaction.
- **Signed-timestamp skew** (F-07 residual, narrowed): the browser now sources
  its action timestamp from the server (`GET /api/auth/time`) rather than an
  unsynchronized wall clock, and the verifier still bounds it to within 300s of
  server time. Within that window a hostile client could still choose the
  recorded value; full closure needs a server-issued timestamp token the client
  echoes and signs. Not attacker-triggerable beyond the ±300s window.
- **Browser-side signing is delivered** (see the identity section): the real
  workbench signs its load-bearing act with a non-extractable WebCrypto Ed25519
  device key. The residual frontier is narrower: multi-device signing keys per
  actor (today one active key per actor rotates the previous), and hardware-
  backed key attestation. The bearer path remains for reads and as an explicit
  no-WebCrypto fallback.
- SSRF redirect-request-fires and DNS-TOCTOU windows (above).
- Access-relative projection of mixed-marking analytic objects (above): today a
  materially-restricted-derived object is raised (and may become invisible to a
  lower context) rather than recomputed from the visible basis.
- Cross-analytic-object reference inheritance: the `append_version` chokepoint
  raises an object to cover the **claims** it rests on (creation, mutation,
  refresh, any caller), but not another **analytic object** whose state it
  embeds — e.g. a warning projecting a restricted forecast's probability band, an
  analogue over a restricted episode. This is guarded at the workbench command
  layer (`project_forecast_warning` sets `most_restrictive([forecast_marking,
  objective_marking])`) and is not reachable via shipped authoring/background
  paths (background `refresh_warnings` only re-projects existing warnings, never
  first-projects over a restricted forecast). A follow-up should extend the
  chokepoint to referenced-object markings so it is the sole fail-closed defense.
- No tool-execution/sandbox surface exists in the product, so tool-sandbox
  hardening is not applicable to the current runtime.

See `CURUNIR_V6_7_FINDINGS.md` for the adversarial finding ledger.

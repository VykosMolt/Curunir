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

- **Cryptographic actor identity / action signing (§13–18) is NOT delivered.**
  The venv has no asymmetric-crypto library and adding a dependency is
  sign-off-gated. Actor identity remains the V6.6 static bearer-token registry;
  attribution is hash-chained but not signed by actor-held keys. This is a
  deployment-hardening limitation pending a dependency decision, not a product
  defect. Recommended next step: enroll Ed25519 actor keys (`cryptography`),
  challenge-response short-lived sessions, and per-action signatures verified on
  replay.
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

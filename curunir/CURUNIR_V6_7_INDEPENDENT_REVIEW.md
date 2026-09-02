# Curunír V6.7 independent adversarial review

## Frozen target and disposition

Grok 4.6 xhigh independently reviewed clean-rewrite commit
`cf61eebde40cab95a90853059473bf3eee72906f` read-only.  It verified the
archive/base/rewrite custody claims and the baseline-residual accounting, but
returned `REJECT` / `DO_NOT_MERGE` because production probes demonstrated two
write-downs and four material coverage/identity defects.  That rejection
superseded the primary engineer's earlier merge recommendation.

## Findings retained

| ID | Severity | Finding | Repair and causal lock |
|---|---|---|---|
| `V67-REV-C1` | Critical | A SPECIAL indicator rationale entered a PUBLIC forecast and transitions. | Indicator execution now cites the indicator/evidence and canonicalizes lower forecast text; `test_restricted_indicator_prose_never_enters_public_forecast`. |
| `V67-REV-C2` | Critical | A SPECIAL theme title entered PUBLIC collection discriminators and requirements. | Collection questions cite source IDs; discriminators retain typed `source_refs`; requirements cite their discriminator; `test_collection_cites_and_inherits_restricted_analytic_source`. |
| `V67-REV-M1` | Major | Same-key re-enrollment raised `NameError`. | `_enroll` returns the already-active record without append; `test_active_key_reenroll_is_idempotent`. |
| `V67-REV-M2` | Major | A newcomer at capacity could evict another principal's challenge/session. | Both bounded maps refuse when the caller owns no eviction candidate; two newcomer-at-capacity locks. |
| `V67-REV-M3` | Major | Evidence reference carriers were missing and schema completeness was not enforced. | Fired/resolution evidence and typed source references are material; every identifier/reference-shaped contract field must have an explicit policy classification. |
| `V67-REV-M4` | Major | Historical locks for distinct analytic quote-without-cite paths were absent. | Restored production locks for indicator rationale, observation values, collection derivation, and analogue titles. |

The review's validator notes were also repaired: focused/product collection
and skip bounds are pinned, and the exact 18 accepted-baseline error nodes are
recorded so a failure/error kind change is refused.  Signed report approval
derives its expected version from the signed target token, not the unsigned
HTTP compatibility field.

The reviewer made no repository edits.

## Second review and retained findings

Grok 4.6 xhigh independently reviewed repair commit
`a8b1a2aa5a3a2d3f095414277e8e8f04716eafb3` read-only and again returned
`REJECT` / `DO_NOT_MERGE`.  It confirmed the claimed closures from the first
review, then found one sibling write-down and one retry-semantics defect:

| ID | Severity | Finding | Repair and causal lock |
|---|---|---|---|
| `V67-REV2-C1` | Critical | Arming a SPECIAL indicator copied its description into a PUBLIC forecast version; later updates left the leaking version visible in history. | Forecast folding now records only the indicator ID. The indicator-prose lock plants separate secrets in description and rationale and inspects every PUBLIC-visible forecast version. |
| `V67-REV2-m1` | Minor | Session-capacity refusal consumed an otherwise valid, verified challenge. | Authentication restores the pending challenge only when capacity admission refuses; the retry lock proves the same signed nonce succeeds after capacity is released. |

The second reviewer made no repository edits.

## Third review and acceptance

Grok 4.6 xhigh independently reviewed repair commit
`6d014bda5d7d7561a5f2bf830684ead8bb3b6f7f` read-only and returned
`ACCEPT` / `MERGE`.  It found no critical, major, or minor defect.  The
reviewer independently established all of the following:

- SPECIAL indicator description and rationale are absent from every
  PUBLIC-visible forecast version, transition, and review item across
  `APPLY_PROBABILITY`, `REVIEW_ONLY`, multiple forecasts, idempotent re-arm,
  and firing;
- restoring the old description interpolation makes the history-wide lock
  fail, while an irrelevant indicator-history perturbation remains stable;
- session-capacity refusal preserves the verified nonce without changing a
  victim session, the same signature succeeds once after capacity is
  released, and deleting nonce restoration makes that retry lock fail;
- expired, invalid-signature, and wrong-actor challenges retain their
  pre-existing consume-on-attempt behavior, actor-owned eviction remains
  confined to the actor, and four concurrent newcomers cannot evict the
  victim;
- both retained terminal reports have the recorded hashes and agree on 130
  focused tests, 630 product-plane tests, 5,676 full-suite tests, zero
  rewrite-only nonpasses, zero outcome-kind changes, and identical clean
  reconstruction identities.

The accepted nonblocking notes are that indicator-owned `ARMED` transitions
may quote their own SPECIAL description, PUBLIC forecasts retain an
association-only indicator ID, and invalid or confused authentication
attempts consume unguessable one-time nonces.  The reviewer did not rerun the
complete terminal validator or live network tests; it hash-checked the two
primary engineer terminal reports and executed focused causal, concurrency,
identity, and product probes.  It made no repository, ref, worktree, or
dependency edits.  This independent disposition closes the review gate for
the executable tree at `6d014bda5d7d7561a5f2bf830684ead8bb3b6f7f`.

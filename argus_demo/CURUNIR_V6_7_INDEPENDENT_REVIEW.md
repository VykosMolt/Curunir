# Curunír V6.7 independent adversarial review

## Frozen target and disposition

Grok 4.6 xhigh independently reviewed clean-rewrite commit
`300ee0c38350b30b56762396ce308f4414d65227` read-only.  It verified the
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

The reviewer made no repository edits.  Closure of these findings requires a
fresh independent review of the repair commit; this document does not claim
that second review has occurred.

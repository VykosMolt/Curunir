# Curunír V6.7 clean architecture contract

This contract is frozen before implementation. Repository evidence at
`archive/curunir-v67-round38-33330efe6ae3`, not that branch's architecture, is
the behavioral reference. The rewrite starts at accepted V6.6 commit
`97053154fd73707fdb9b66509bf4490a8f5a4ce6`.

## Objective and scope

V6.7 adds information-flow integrity, authoritative control gates,
cryptographic actor actions, guarded egress, bounded hostile-input handling,
durable backup/import/recovery, and reproducible validation without removing or
renaming accepted product-plane capabilities. Netwatch and the closed V6.1
campaign are outside scope.

## Small trusted core

1. `curunir_operational/canonical.py` owns strict canonical values. Records,
   signatures, manifests, and imported values pass the same finite,
   Unicode-well-formed, deterministic JSON rule.
2. `curunir_operational/security.py` owns the typed material-reference registry,
   transitive reference closure, and marking admission. Every reference-shaped
   schema field is classified as material or explicitly non-material. A new
   unclassified reference field fails the registry-completeness test.
3. `MissionDataStore.append` is the unavoidable record admission point. It
   floors a marked record on its previous version and all resolved material
   dependencies before hashing. Import never repairs authenticated history; it
   validates the complete artifact before installation.
   Access projections independently filter every embedded transition and
   resolution, and derive visible workflow status only from visible children;
   a visible parent is never a carrier for a more-restricted audit child.
4. `curunir_analytic/substrate.py::append_version` and
   `record_transition` remain the domain-facing version/transition APIs, but
   delegate marking admission to the shared security policy. Callers provide
   typed evidence references; derived prose cites identifiers rather than
   copying more-restricted text into a lower record.
5. `curunir_workbench/authority.py` owns one authoritative basis walk for report
   gates. It reads raw state, traverses typed dependencies, checks current
   official status and open reviews, and then applies visibility. Projection
   helpers never decide whether an action is allowed.
6. `curunir_operational/store.py` owns five boring file primitives: strict
   regular-file read, atomic file write, append under one lock, complete
   preflight, and staged install. Full restore and delta import share those
   primitives and do not duplicate installation semantics.
7. `curunir_identity` owns Ed25519 keys, sessions, signed-action verification,
   and historical replay. Verification is pure; authorization executes next;
   the signed-action audit record is committed only after success.
8. `curunir_fabric/transport.py` is the only product public-web transport. It
   validates every initial/redirect destination before request, resolves each
   hop once, connects only to the validated public address set, and spends one
   total deadline across address failover, response, and redirects. Connector
   overrides remain an offline-test seam, not a second production transport.
9. `curunir_analytic/providers.py` resolves both declared input references and
   record identifiers carried in provider cargo against raw state before the
   call. Hostile output is retained as an invalid inference, never converted
   into plausible analytical content.
10. Acquisition, provider, XML, subprocess, and HTTP render edges normalize or
    refuse hostile values once at entry. Resource exhaustion is a failure or
    unresolved observation, never evidence of absence.

## Invariants and forbidden shortcuts

The machine-readable ledger is `CURUNIR_V6_7_INVARIANTS.json`. The rewrite may
reuse an old definition or test only after direct inspection shows it is small,
canonical, and compatible with this architecture. It may not reuse the old
store/import/report-policy implementation wholesale.

Forbidden shortcuts are mocks for production paths, test-only branches,
hard-coded pass outputs, swallowed exceptions, filtered-view authorization,
post-install validation, caller-selected signed identity, untracked required
fixtures, and tests that only restate the implementation.

## Failure semantics

- Unrepresentable marking joins produce an auditable deny-all marking for
  background derivation; interactive authority changes refuse loudly.
- Unknown, malformed, non-canonical, stale, replayed, incompatible, corrupt, or
  tampered inputs fail closed with typed errors and no partial authoritative
  state.
- A source/content fault is contained and recorded as source failure; a local
  custody, memory, or store-integrity fault propagates.
- Crash recovery repairs only a provably uncommitted torn tail. A committed bad
  chain or corrupt payload is refused and preserved for diagnosis.
- A gate that cannot establish clean authoritative basis refuses the action.

## Causal evidence plan

Each load-bearing family has a positive path, a historical exploit, and a
mechanism mutation or bypass probe. The terminal suite must show that disabling
marking admission, signature verification, authoritative raw-state walking, or
import preflight makes its corresponding exploit test fail while irrelevant
perturbations remain stable. These probes are local test substitutions, not a
general mutation-testing framework.

## Escalation conditions

Stop rather than improvise if current code shows an externally meaningful
behavior that this contract would delete, if an accepted pre-V6.7 capability
cannot pass through the choke points, if the external kernel hash cannot be
established, or if a security property needs a scope-changing product decision.

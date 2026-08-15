"""Curunír semantic plane — evidence understanding, world model, active collection.

Tranche: CURUNIR_V6_WORLD_MODEL_AND_ACTIVE_COLLECTION.

Turns preserved OSINT-fabric manifestations into meaning and closes the first
full intelligence loop:

    manifestation → normalized document/record → evidence-addressed
    observations → entity/relation/event world model (bitemporal, reversible
    identity, evidence-bound) → semantic claims with lifecycle → semantic
    change detection → affected-object propagation → hypothesis support →
    discriminating observations → information requirements → ranked
    collection routes → OSINT fabric execution → new evidence → update.

One world: state lives in curunir_operational records (ObjectVersion /
RelationshipVersion / ActivityRecord) inside the same hash-chained store as
mission and fabric events. This package adds the understanding, proposition
ledger, change engine and collection planner around that shared plane; it
reuses the V4/V5 semantic engines (hypotheses, contradiction, dependence,
lifecycle, typed observations) rather than reinventing them.

Boundaries preserved: every semantic object descends to a manifestation and
span/field; valid time and knowledge time stay distinct; identity merges stay
reversible proposals; machine extraction lands as EXTRACTED/UNREVIEWED state,
never as reviewed truth; translations remain derivative; dependent sources
never count as independent corroboration.
"""

PACKAGE_VERSION = "curunir-semantic-0.1"
PARSER_VERSION = "curunir-semantic-parsers-0.1"

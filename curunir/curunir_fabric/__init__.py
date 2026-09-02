"""Curunír OSINT Fabric — persistent, source-aware public collection plane.

Tranche: CURUNIR_V6_OSINT_FABRIC_FOUNDATION.

Composes the ARGUS Source Intelligence layer (source descriptors, access
policy, bounded acquisition, immutable custody) with the operational
mission-data substrate (hash-chained event store, information requirements,
alerts) into one collection loop:

    information need → capable sources → typed multilingual discovery plan
    → policy-gated acquisition → immutable native evidence + manifestations
    → coverage and failure accounting → pivots → persistent watches.

Boundaries preserved: origin evidence is immutable and content-addressed;
"no result" is recorded as EXECUTED_EMPTY, never as proof of absence; model
proposals carry origin=MODEL and never bypass source policy; identity
correlation stays reversible (pivots are proposals, not merges); no code in
this package writes canonical ARGUS PostgreSQL state.
"""

PACKAGE_VERSION = "curunir-fabric-0.1"
PLANNER_VERSION = "fabric-planner-0.1"
ABSENCE_SEMANTICS = "ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE"

"""Bounded distributed collaboration and operator-evaluation research plane.

V3 is intentionally isolated from the canonical ARGUS database.  It reuses
the operational canonical-serialization seam and ARGUS evidence references,
but persists independently hash-chained node histories below caller-supplied
store roots.  Authentication is a deterministic HMAC test facility only; it
is not production identity federation or PKI.
"""

PROTOCOL_VERSION = "curunir-distributed-sync-v3"
SCHEMA_VERSION = "curunir-distributed-event-v3"
POLICY_VERSION = "curunir-bounded-authorization-v3"
AUTHENTICATION_STATUS = "TEST_SIGNER_ONLY"


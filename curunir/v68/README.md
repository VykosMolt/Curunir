# v68 — frozen mission fixtures

Deterministic, explicitly **notional** fixtures for the V6.8 missions that do
not use live sources.

`fixtures/m2_regulatory/` is the M2 regulatory-correction bundle: a structured
public notice, an unstructured bulletin, a dependent Croatian derivative
labelled as dependent, and a later correction that supersedes the original. It
exists to exercise one specific question — after a public notice is corrected,
which propositions remain supported at each valid and knowledge time, and which
earlier conclusion must be revised — including the trap that the translated
derivative must not be cited as independent corroboration of the superseded
claim.

The bytes are frozen and hash-listed in the mission's `input_manifest.json`.
Changing a fixture invalidates every campaign root prepared against it.

**Nothing here refers to a real regulator, company or person.** Scenario truth
is the fixture manifest and the exact bytes; no claim extends beyond them.

Mission definitions: `../CURUNIR_V6_8_MISSIONS.json`.
Results: `../../curunir_v68_runs/`.

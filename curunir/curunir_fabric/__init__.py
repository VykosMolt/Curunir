"""Collection plane: registered sources, guarded acquisition, custody, watches.

An empty result is recorded as EXECUTED_EMPTY, never as proof that something
does not exist. Pivots are proposals, never merges. Nothing here writes
canonical ARGUS PostgreSQL state.
"""

PACKAGE_VERSION = "curunir-fabric-0.1"
PLANNER_VERSION = "fabric-planner-0.1"
ABSENCE_SEMANTICS = "ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE"

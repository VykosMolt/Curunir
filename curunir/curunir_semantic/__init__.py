"""Turn retrieved evidence into observations, a world model, and claims.

State lives in the same hash-chained store as mission and fabric events. Every
semantic object descends to a manifestation and a span or field, when something
was true stays separate from when we learned it, and machine extraction is never
recorded as reviewed truth.
"""

PACKAGE_VERSION = "curunir-semantic-0.1"
PARSER_VERSION = "curunir-semantic-parsers-0.1"

"""Command failures that map onto honest HTTP responses.

NotFound covers both "no such record" and "you may not see it"; the two are
deliberately indistinguishable. Any other KeyError is a bug and must surface
as a server error, never as a claim about what exists.
"""


class NotFound(LookupError):
    pass


class Conflict(Exception):
    """Another writer changed the record; the caller's view is stale."""

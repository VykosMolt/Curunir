"""Typed command-layer failures with honest HTTP semantics.

NotFound covers both "does not exist" and "exists but this context may not
see it" — deliberately indistinguishable. A bare KeyError anywhere else is a
bug and must surface as a server error, never as an existence claim.
"""


class NotFound(LookupError):
    pass


class Conflict(Exception):
    """Another writer changed the record; the caller's view is stale."""

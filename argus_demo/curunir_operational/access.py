"""Fail-closed access model: markings, contexts, view checks.

A record is visible only when the context satisfies role, every compartment,
and releasability. Anything unresolved (unknown role, missing marking, unknown
compartment) denies. Consumers must never branch on the existence of records
they cannot view; projections therefore compute every count from the filtered
set only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ROLE_RANK = {"OBSERVER": 0, "ANALYST": 1, "SUPERVISOR": 2}
ACTOR_KINDS = ("HUMAN", "SERVICE")


@dataclass(frozen=True)
class Marking:
    owning_authority: str
    compartments: tuple[str, ...] = ()
    releasability: tuple[str, ...] = ()
    min_role: str = "OBSERVER"
    caveats: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.owning_authority:
            raise ValueError("marking requires an owning authority")
        if self.min_role not in ROLE_RANK:
            raise ValueError(f"unknown role: {self.min_role}")
        object.__setattr__(self, "compartments", tuple(sorted(self.compartments)))
        object.__setattr__(self, "releasability", tuple(sorted(self.releasability)))
        object.__setattr__(self, "caveats", tuple(self.caveats))

    def to_record(self) -> dict[str, Any]:
        return {"owning_authority": self.owning_authority, "compartments": list(self.compartments),
                "releasability": list(self.releasability), "min_role": self.min_role, "caveats": list(self.caveats)}


def marking_from_record(record: Mapping[str, Any]) -> Marking:
    return Marking(record["owning_authority"], tuple(record.get("compartments", ())),
                   tuple(record.get("releasability", ())), record.get("min_role", "OBSERVER"),
                   tuple(record.get("caveats", ())))


@dataclass(frozen=True)
class AccessContext:
    context_id: str
    actor_id: str
    actor_kind: str
    roles: tuple[str, ...]
    compartments: tuple[str, ...] = ()
    releasability: tuple[str, ...] = ()
    organisation: str = ""

    def __post_init__(self):
        if self.actor_kind not in ACTOR_KINDS:
            raise ValueError(f"unknown actor kind: {self.actor_kind}")
        unknown = [r for r in self.roles if r not in ROLE_RANK]
        if unknown:
            raise ValueError(f"unknown roles: {unknown}")

    @property
    def max_role_rank(self) -> int:
        return max((ROLE_RANK[r] for r in self.roles), default=-1)

    def to_record(self) -> dict[str, Any]:
        return {"context_id": self.context_id, "actor_id": self.actor_id, "actor_kind": self.actor_kind,
                "roles": list(self.roles), "compartments": list(self.compartments),
                "releasability": list(self.releasability), "organisation": self.organisation}


def context_from_record(record: Mapping[str, Any]) -> AccessContext:
    return AccessContext(record["context_id"], record["actor_id"], record["actor_kind"],
                         tuple(record["roles"]), tuple(record.get("compartments", ())),
                         tuple(record.get("releasability", ())), record.get("organisation", ""))


def can_view(marking: Marking | Mapping[str, Any] | None, context: AccessContext) -> bool:
    if marking is None:
        return False
    record = marking.to_record() if isinstance(marking, Marking) else marking
    min_role = record.get("min_role")
    if min_role not in ROLE_RANK or context.max_role_rank < ROLE_RANK[min_role]:
        return False
    if not set(record.get("compartments", ())) <= set(context.compartments):
        return False
    releasability = tuple(record.get("releasability", ()))
    if releasability:
        return bool(set(releasability) & set(context.releasability))
    return bool(record.get("owning_authority")) and context.organisation == record["owning_authority"]


def visible(records: list[dict[str, Any]], context: AccessContext, marking_key: str = "marking") -> list[dict[str, Any]]:
    return [r for r in records if can_view(r.get(marking_key), context)]


# a reserved compartment no legitimate context is ever granted; its presence
# renders a marking unviewable by everyone (can_view requires the record's
# compartments to be a SUBSET of the context's). Never configure a context or
# source with this compartment.
UNJOINABLE_SEAL_COMPARTMENT = "curunir:unjoinable-seal"


def _seal(markings: list["Marking"]) -> "Marking":
    """The safe marking for inputs that admit no single-marking join
    (cross-authority, or disjoint/orthogonal releasability): a **deny-all** seal.

    A single Marking cannot express an orthogonal-axis conjunction — "org X AND
    releasable to REL_A" — because releasability and organisation are alternative
    axes in `can_view`; that impossibility is exactly why `most_restrictive`
    raises. Any *satisfiable* single-marking seal would therefore be viewable by
    some context that cannot view one of the inputs (a write-down). The only
    single Marking that never under-classifies is one viewable by no one: it
    carries the reserved unviewable compartment. The derived record still exists
    in the log (auditable, replayable) but is withheld from every projection
    until a human reclassifies it explicitly — matching the fail-closed
    "classify the derived record explicitly" rule, without aborting the pass."""
    records = [m.to_record() for m in markings]
    compartments: set[str] = {UNJOINABLE_SEAL_COMPARTMENT}
    caveats: list[str] = []
    for record in records:
        compartments |= set(record.get("compartments", ()))
        caveats += list(record.get("caveats", ()))
    caveats.append("SEALED_UNJOINABLE_MARKINGS")
    return Marking(owning_authority=records[0]["owning_authority"],
                   compartments=tuple(sorted(compartments)),
                   releasability=(), min_role="SUPERVISOR",
                   caveats=tuple(dict.fromkeys(caveats)))


def inherited_marking(base: "Marking",
                      references: "list[Marking | Mapping[str, Any] | None]") -> "Marking":
    """The marking a derived record must carry: the high-water mark of its own
    (or its context's) marking and every material reference whose state it
    embeds. `references` are Markings, marking-records, or None (ignored). With
    no resolvable reference this is `base` unchanged — a record that cites
    nothing keeps its context marking exactly, so ordinary low-marked writes
    are untouched.

    This is TOTAL: when a derived record embeds more restricted state it raises
    to the join, and when no safe single-marking join exists it SEALS
    (over-restricts) rather than raising, so a background watch/propagation pass
    never aborts on an un-joinable pair and never under-classifies. Interactive
    command guards that must refuse loudly call `most_restrictive` directly,
    which still raises."""
    markings = [base]
    for ref in references:
        if ref is None:
            continue
        markings.append(ref if isinstance(ref, Marking) else marking_from_record(ref))
    try:
        return most_restrictive(markings)
    except ValueError:
        return _seal(markings)


def most_restrictive(markings: list[Marking]) -> Marking:
    """The marking a record derived from several subjects must carry: it may
    be viewed only by a context that could view every input. Compartments
    union (needs all), releasability intersects (fewer options), min_role
    takes the maximum. A record so marked can never be less restricted than
    any subject it is about — the high-water mark that stops write-down."""
    markings = [m for m in markings if m is not None]
    if not markings:
        raise ValueError("most_restrictive requires at least one marking")
    records = [m.to_record() if isinstance(m, Marking) else dict(m) for m in markings]
    compartments: set[str] = set()
    releasabilities: list[set[str]] = []
    for record in records:
        compartments |= set(record.get("compartments", ()))
        releasabilities.append(set(record.get("releasability", ())))
    # intersection across those that declare releasability; a marking with no
    # releasability is owning-authority-only (strictly more restrictive) and
    # therefore collapses the shared set to empty
    declared = [r for r in releasabilities if r]
    if len(declared) < len(releasabilities):
        releasability: tuple[str, ...] = ()
    elif declared:
        shared = set.intersection(*declared)
        releasability = tuple(sorted(shared))
    else:
        releasability = ()
    min_role = max((record.get("min_role", "OBSERVER") for record in records),
                   key=lambda role: ROLE_RANK.get(role, 0))
    caveats: tuple[str, ...] = ()
    for record in records:
        caveats += tuple(record.get("caveats", ()))
    authorities = {record["owning_authority"] for record in records}
    # An empty-releasability marking is ORG-LOCKED: can_view falls back to
    # context.organisation == owning_authority — a DIFFERENT visibility axis
    # than releasability, not a stricter point on the same one. If the join
    # collapses to org-locked while any input DECLARED releasability, the
    # result is viewable by an org member who could NOT view that releasable
    # input (releasable markings ignore organisation), a write-down. And a
    # single owning_authority cannot express "viewable only by members of
    # both auth-A and auth-B". No single Marking is a safe join in either
    # case — fail closed rather than declassify. Same-authority, same-axis
    # joins (the mission's own case) are exact.
    any_releasable = any(r for r in releasabilities)
    if not releasability and (len(authorities) > 1 or any_releasable):
        raise ValueError(
            "cannot form a safe single-marking join: the result would be "
            f"org-locked ({sorted(authorities)}) while an input was releasable "
            "or spanned authorities — classify the derived record explicitly")
    return Marking(owning_authority=records[0]["owning_authority"],
                   compartments=tuple(sorted(compartments)),
                   releasability=releasability, min_role=min_role,
                   caveats=tuple(dict.fromkeys(caveats)))

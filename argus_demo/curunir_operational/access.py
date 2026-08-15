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

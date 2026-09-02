"""Themes and issues over the claim ledger.

A theme is typed state: its membership, entities, events, source families,
interval and status are all inspectable, and every change is a recorded
transition. Discovery clusters claims over the world model's own structure and
invents no semantics; analyst acts and accepted model candidates do the rest.
Merge and split are non-destructive — lineage is recorded both ways and the
superseded theme keeps its history.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id

from .basis import basis_changed_materially, compute_basis
from .contracts import ThemeRecord
from .store import AnalyticStore
from .substrate import (AnalyticContext, append_version, creation_authority,
                        ensure_transition, record_transition)

# statuses the machine may derive; the rest come from analyst acts
_MACHINE_STATUSES = ("EMERGING", "ACTIVE", "CONTESTED", "STALE")


def _derive_status(basis) -> str:
    """Status from the live basis.

    Independence promotes a theme, not raw count: one origin family stays
    EMERGING however many derivative manifestations restate it.
    """
    active_support = len(basis.supporting_claim_ids) - basis.degraded_claim_count
    if active_support <= 0:
        return "STALE"
    if basis.contradicting_claim_ids:
        return "CONTESTED"
    if basis.origin_family_count >= 2:
        return "ACTIVE"
    return "EMERGING"


def theme_id_for(title: str, parent_theme_id: str = "") -> str:
    return digest_id("theme", title.casefold().strip(), parent_theme_id)


def create_theme(ctx: AnalyticContext, *, title: str, description: str = "",
                 supporting_claim_ids: Iterable[str],
                 contradicting_claim_ids: Iterable[str] = (),
                 entity_ids: tuple[str, ...] = (), event_ids: tuple[str, ...] = (),
                 relation_ids: tuple[str, ...] = (),
                 parent_theme_id: str = "",
                 provenance_kind: str = "ANALYST", inference_id: str = "",
                 proposal_id: str = "", caused_by: str = "",
                 lineage: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    """Create a theme, idempotent by title and parent.

    Authority follows provenance: DERIVED for a rule, ANALYST_ASSESSMENT for an
    analyst, SUPPORTED_INFERENCE for model output through the candidate gate.
    """
    store = ctx.store
    theme_id = theme_id_for(title, parent_theme_id)
    existing = store.current_themes().get(theme_id)
    if existing is not None:
        # complete a creation whose transitions never landed
        ensure_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                          transition_type="CREATED",
                          detail=f"theme created ({existing['provenance_kind']}, "
                                 f"{existing['status']}): {existing['title'][:120]}",
                          caused_by=caused_by or theme_id,
                          evidence_refs=tuple(
                              existing["basis"]["supporting_claim_ids"][:10]),
                          to_status=existing["status"])
        if existing["parent_theme_id"]:
            record_transition(ctx, subject_kind="analytic_theme",
                              subject_id=existing["parent_theme_id"],
                              transition_type="SUBTHEME_EMERGED",
                              detail=f"subtheme emerged: {existing['title'][:120]}",
                              caused_by=theme_id, evidence_refs=(theme_id,))
        # model provenance may only complete the materialization the human
        # accepted, never modify an object under another or absent proposal
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                       != existing.get("proposal_id")):
            raise ValueError(
                "an existing theme cannot be modified under model provenance "
                "with a different proposal: propose and accept a new candidate")
        # evidence beyond the existing basis is folded in rather than
        # discarded, with the fold's provenance on record
        new_supporting = [c for c in supporting_claim_ids
                          if c not in existing["basis"]["supporting_claim_ids"]]
        new_contradicting = [c for c in contradicting_claim_ids
                             if c not in existing["basis"]["contradicting_claim_ids"]]
        if provenance_kind == "MODEL" and (new_supporting or new_contradicting):
            # a completion may fold only the claims the human accepted
            proposal = store.latest_by_id("analytical_proposal",
                                          "proposal_id").get(proposal_id, {})
            accepted_claims = set(proposal.get("content", {})
                                  .get("supporting_claim_ids", ()))
            beyond = [c for c in new_supporting + new_contradicting
                      if c not in accepted_claims]
            if beyond:
                raise ValueError(
                    f"model fold includes {len(beyond)} claim(s) beyond the "
                    "accepted candidate's content: the human accepted specific "
                    "evidence, not an open mandate")
        if new_supporting or new_contradicting:
            return update_membership(
                ctx, theme_id, add_supporting=new_supporting,
                add_contradicting=new_contradicting,
                caused_by=caused_by or theme_id,
                rationale=f"creation over an existing theme folds the new "
                          f"evidence (provenance {provenance_kind}"
                          f"{', proposal ' + proposal_id[:18] if proposal_id else ''})")
        return existing
    authority = creation_authority(
        store, provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id, target_kind="analytic_theme",
        materialized={"title": title,
                      "supporting_claim_ids": tuple(supporting_claim_ids)})
    basis = compute_basis(store, supporting_claim_ids, contradicting_claim_ids)
    status = _derive_status(basis)
    record = ThemeRecord(
        theme_id=theme_id, version=1, title=title, description=description,
        status=status, authority=authority,
        parent_theme_id=parent_theme_id, lineage=lineage, basis=basis,
        entity_ids=entity_ids, event_ids=event_ids, relation_ids=relation_ids,
        valid_from=basis.stated_valid_from or None, valid_to=None,
        provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "",
        change_reason="",
        history=(f"CREATED:{provenance_kind}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                      transition_type="CREATED",
                      detail=f"theme created ({provenance_kind}, {status}): {title[:120]}",
                      caused_by=caused_by or theme_id,
                      evidence_refs=tuple(basis.supporting_claim_ids[:10]),
                      to_status=status)
    if parent_theme_id:
        # keyed by the subtheme id, so every subtheme's emergence is recorded
        # and a crashed run still completes on the exists path
        record_transition(ctx, subject_kind="analytic_theme",
                          subject_id=parent_theme_id,
                          transition_type="SUBTHEME_EMERGED",
                          detail=f"subtheme emerged: {title[:120]}",
                          caused_by=theme_id, evidence_refs=(theme_id,))
    return appended


def _reappend(ctx: AnalyticContext, theme: Mapping[str, Any], updates: dict[str, Any],
              change_reason: str, history_note: str) -> dict[str, Any]:
    merged = {k: v for k, v in theme.items() if k != "record_type"}
    merged.update(updates)
    merged["version"] = ctx.store.next_analytic_version("analytic_theme", theme["theme_id"])
    merged["change_reason"] = change_reason
    merged["history"] = tuple(theme["history"]) + (history_note,)
    merged["recorded_time"] = ctx.now_fn()
    merged["marking"] = ctx.marking
    for key in ("entity_ids", "event_ids", "relation_ids", "history"):
        merged[key] = tuple(merged[key])
    merged["lineage"] = tuple(tuple(pair) for pair in merged["lineage"])
    if isinstance(merged["basis"], Mapping):
        from .basis import basis_from_record
        merged["basis"] = basis_from_record(merged["basis"])
    record = ThemeRecord(**merged)
    return append_version(ctx, record)


def update_membership(ctx: AnalyticContext, theme_id: str, *,
                      add_supporting: Iterable[str] = (),
                      add_contradicting: Iterable[str] = (),
                      caused_by: str, rationale: str) -> dict[str, Any]:
    """Change theme membership as a new version.

    Contradicting claims are added; they never delete support.
    """
    store = ctx.store
    theme = store.current_themes().get(theme_id)
    if theme is None:
        raise ValueError(f"unknown theme: {theme_id}")
    supporting = tuple(dict.fromkeys(
        tuple(theme["basis"]["supporting_claim_ids"]) + tuple(add_supporting)))
    contradicting = tuple(dict.fromkeys(
        tuple(theme["basis"]["contradicting_claim_ids"]) + tuple(add_contradicting)))
    basis = compute_basis(store, supporting, contradicting)
    changes = basis_changed_materially(theme["basis"], basis)
    if not changes:
        return theme
    status = _derive_status(basis) \
        if theme["status"] in _MACHINE_STATUSES else theme["status"]
    updated = _reappend(ctx, theme,
                        {"basis": basis, "status": status,
                         "valid_from": basis.stated_valid_from or theme["valid_from"]},
                        change_reason=rationale[:300],
                        history_note=f"MEMBERSHIP:{','.join(changes)}")
    record_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                      transition_type="MEMBERSHIP_CHANGED",
                      detail=f"{rationale[:200]} ({', '.join(changes)})",
                      caused_by=caused_by,
                      evidence_refs=tuple(add_supporting)[:5] + tuple(add_contradicting)[:5],
                      from_status=theme["status"], to_status=status)
    if tuple(add_contradicting):
        record_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                          transition_type="CONTRADICTION_ADDED",
                          detail=f"contradicting evidence linked: {rationale[:180]}",
                          caused_by=caused_by,
                          evidence_refs=tuple(add_contradicting)[:5],
                          from_status=theme["status"], to_status=status)
    return updated


def refresh_theme(ctx: AnalyticContext, theme_id: str, *, caused_by: str) -> dict[str, Any]:
    """Re-derive a theme from its member claims, appending only on change."""
    store = ctx.store
    theme = store.current_themes().get(theme_id)
    if theme is None:
        raise ValueError(f"unknown theme: {theme_id}")
    if theme["status"] in ("MERGED", "SPLIT", "RESOLVED"):
        return theme  # a closed theme is not machine-reopened
    basis = compute_basis(store, theme["basis"]["supporting_claim_ids"],
                          theme["basis"]["contradicting_claim_ids"])
    changes = basis_changed_materially(theme["basis"], basis)
    status = _derive_status(basis)
    if not changes and status == theme["status"]:
        return theme
    updated = _reappend(ctx, theme, {"basis": basis, "status": status},
                        change_reason=f"refresh after {caused_by[:60]}: "
                                      f"{', '.join(changes) or 'status change'}",
                        history_note=f"REFRESHED:{theme['status']}->{status}")
    before_families = set(theme["basis"]["origin_families"])
    after_families = set(basis.origin_families)
    if before_families != after_families:
        gained, lost = after_families - before_families, before_families - after_families
        record_transition(
            ctx, subject_kind="analytic_theme", subject_id=theme_id,
            transition_type="SOURCE_DIVERSITY_CHANGED",
            detail=f"origin families {len(before_families)}→{len(after_families)}"
                   f" (+{len(gained)}/-{len(lost)}); reach alone never counts",
            caused_by=caused_by, from_status=theme["status"], to_status=status)
    if status != theme["status"]:
        transition_type = {"CONTESTED": "CONTRADICTION_ADDED", "STALE": "STALE",
                           "ACTIVE": "STRENGTHENED", "EMERGING": "WEAKENED"}[status]
        record_transition(
            ctx, subject_kind="analytic_theme", subject_id=theme_id,
            transition_type=transition_type,
            detail=f"status {theme['status']} → {status}: "
                   f"{basis.origin_family_count} independent famil"
                   f"{'y' if basis.origin_family_count == 1 else 'ies'}, "
                   f"{len(basis.contradicting_claim_ids)} contradiction(s), "
                   f"{basis.degraded_claim_count} degraded",
            caused_by=caused_by, from_status=theme["status"], to_status=status)
    if "evidence" in changes and status == theme["status"]:
        record_transition(
            ctx, subject_kind="analytic_theme", subject_id=theme_id,
            transition_type="EVIDENCE_UPDATED",
            detail=f"member claim evidence advanced "
                   f"({theme['basis']['observation_count']}→"
                   f"{basis.observation_count} observation(s), latest "
                   f"{basis.latest_time[:19]})",
            caused_by=caused_by, from_status=theme["status"], to_status=status)
    if basis.degraded_claim_count > theme["basis"]["degraded_claim_count"]:
        record_transition(
            ctx, subject_kind="analytic_theme", subject_id=theme_id,
            transition_type="WEAKENED",
            detail=f"supporting basis degraded: {basis.degraded_claim_count} of "
                   f"{len(basis.supporting_claim_ids)} member claims no longer CURRENT",
            caused_by=caused_by, from_status=theme["status"], to_status=status)
    return updated


def resolve_theme(ctx: AnalyticContext, theme_id: str, *, actor_id: str,
                  actor_kind: str, note: str) -> dict[str, Any]:
    """Close a theme; only a human may."""
    if actor_kind != "HUMAN":
        raise ValueError("resolving a theme is an analyst act")
    theme = ctx.store.current_themes().get(theme_id)
    if theme is None:
        raise ValueError(f"unknown theme: {theme_id}")
    if theme["status"] == "RESOLVED":
        return theme  # already resolved
    updated = _reappend(ctx, theme, {"status": "RESOLVED"},
                        change_reason=f"resolved by {actor_id}: {note[:200]}",
                        history_note=f"RESOLVED:{actor_id}")
    record_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                      transition_type="RESOLVED", detail=note[:300],
                      caused_by=f"analyst:{actor_id}",
                      from_status=theme["status"], to_status="RESOLVED")
    return updated


# ---- merge / split (lineage-preserving) -----------------------------------


def propose_merge(ctx: AnalyticContext, left_theme_id: str, right_theme_id: str,
                  *, rationale: str, caused_by: str = "") -> None:
    for theme_id, other in ((left_theme_id, right_theme_id),
                            (right_theme_id, left_theme_id)):
        record_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                          transition_type="MERGE_PROPOSED",
                          detail=f"possible equivalence with {other[:24]}: {rationale[:200]}",
                          caused_by=caused_by or f"merge:{left_theme_id[:12]}:{right_theme_id[:12]}",
                          evidence_refs=(other,))


def apply_merge(ctx: AnalyticContext, into_theme_id: str, from_theme_id: str, *,
                actor_id: str, actor_kind: str, rationale: str) -> dict[str, Any]:
    """Merge one theme into another, recording lineage both ways.

    The survivor absorbs the basis; the absorbed theme becomes MERGED and keeps
    its history.
    """
    if actor_kind != "HUMAN":
        raise ValueError("merging themes is an analyst act: equivalence is a judgment")
    store = ctx.store
    into = store.current_themes().get(into_theme_id)
    absorbed = store.current_themes().get(from_theme_id)
    if into is None or absorbed is None:
        raise ValueError("both themes must exist to merge")
    if ("MERGED_FROM", from_theme_id) in {tuple(p) for p in into["lineage"]}:
        # already merged: complete any missing tail, append nothing
        for theme_id in (into_theme_id, from_theme_id):
            ensure_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                              transition_type="MERGED",
                              detail=f"{from_theme_id[:24]} merged into "
                                     f"{into_theme_id[:24]} by {actor_id}: {rationale[:180]}",
                              caused_by=f"analyst:{actor_id}:merge:{into_theme_id[:12]}:{from_theme_id[:12]}",
                              evidence_refs=(into_theme_id, from_theme_id))
        if absorbed["status"] != "MERGED":
            _reappend(ctx, absorbed,
                      {"status": "MERGED",
                       "lineage": tuple(tuple(pair) for pair in absorbed["lineage"])
                       + ((("MERGED_INTO", into_theme_id),)
                          if ("MERGED_INTO", into_theme_id) not in
                          {tuple(p) for p in absorbed["lineage"]} else ())},
                      change_reason=f"merged into {into_theme_id[:24]} by {actor_id}",
                      history_note=f"MERGED_INTO:{into_theme_id[:18]}")
        return into
    if absorbed["status"] == "MERGED":
        raise ValueError(f"theme {from_theme_id[:24]} is already merged elsewhere; "
                         "its lineage records where")
    supporting = tuple(dict.fromkeys(
        tuple(into["basis"]["supporting_claim_ids"])
        + tuple(absorbed["basis"]["supporting_claim_ids"])))
    contradicting = tuple(dict.fromkeys(
        tuple(into["basis"]["contradicting_claim_ids"])
        + tuple(absorbed["basis"]["contradicting_claim_ids"])))
    basis = compute_basis(store, supporting, contradicting)
    survivor = _reappend(
        ctx, into,
        {"basis": basis,
         "status": _derive_status(basis),
         "entity_ids": tuple(dict.fromkeys(tuple(into["entity_ids"])
                                           + tuple(absorbed["entity_ids"]))),
         "event_ids": tuple(dict.fromkeys(tuple(into["event_ids"])
                                          + tuple(absorbed["event_ids"]))),
         "relation_ids": tuple(dict.fromkeys(tuple(into["relation_ids"])
                                             + tuple(absorbed["relation_ids"]))),
         "lineage": tuple(tuple(pair) for pair in into["lineage"])
         + (("MERGED_FROM", from_theme_id),)},
        change_reason=f"merge of {from_theme_id[:24]} by {actor_id}: {rationale[:200]}",
        history_note=f"MERGED_FROM:{from_theme_id[:18]}")
    _reappend(ctx, absorbed,
              {"status": "MERGED",
               "lineage": tuple(tuple(pair) for pair in absorbed["lineage"])
               + (("MERGED_INTO", into_theme_id),)},
              change_reason=f"merged into {into_theme_id[:24]} by {actor_id}",
              history_note=f"MERGED_INTO:{into_theme_id[:18]}")
    for theme_id in (into_theme_id, from_theme_id):
        record_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                          transition_type="MERGED",
                          detail=f"{from_theme_id[:24]} merged into {into_theme_id[:24]} "
                                 f"by {actor_id}: {rationale[:180]}",
                          caused_by=f"analyst:{actor_id}:merge:{into_theme_id[:12]}:{from_theme_id[:12]}",
                          evidence_refs=(into_theme_id, from_theme_id))
    return survivor


def apply_split(ctx: AnalyticContext, theme_id: str,
                partitions: list[tuple[str, tuple[str, ...]]], *,
                actor_id: str, actor_kind: str, rationale: str) -> list[dict[str, Any]]:
    """Split a theme into parts, recording lineage both ways.

    The original becomes SPLIT and keeps its history; each part records where
    it came from.
    """
    if actor_kind != "HUMAN":
        raise ValueError("splitting a theme is an analyst act")
    store = ctx.store
    theme = store.current_themes().get(theme_id)
    if theme is None:
        raise ValueError(f"unknown theme: {theme_id}")
    if theme["status"] == "SPLIT":
        # already split: return the recorded parts
        part_ids = [other for kind, other in
                    (tuple(p) for p in theme["lineage"]) if kind == "SPLIT_INTO"]
        current = store.current_themes()
        return [current[part_id] for part_id in part_ids if part_id in current]
    parts = []
    for title, claim_ids in partitions:
        part = create_theme(
            ctx, title=title, description=f"split from: {theme['title'][:120]}",
            supporting_claim_ids=claim_ids,
            provenance_kind="ANALYST",
            caused_by=f"analyst:{actor_id}:split:{theme_id[:12]}",
            lineage=(("SPLIT_FROM", theme_id),))
        parts.append(part)
    _reappend(ctx, theme,
              {"status": "SPLIT",
               "lineage": tuple(tuple(pair) for pair in theme["lineage"])
               + tuple(("SPLIT_INTO", part["theme_id"]) for part in parts)},
              change_reason=f"split by {actor_id}: {rationale[:200]}",
              history_note=f"SPLIT_INTO:{len(parts)}")
    record_transition(ctx, subject_kind="analytic_theme", subject_id=theme_id,
                      transition_type="SPLIT",
                      detail=f"split into {len(parts)} themes by {actor_id}: "
                             f"{rationale[:180]}",
                      caused_by=f"analyst:{actor_id}:split",
                      evidence_refs=tuple(part["theme_id"] for part in parts))
    return parts


# ---- deterministic discovery ----------------------------------------------


def discover_theme_candidates(store: AnalyticStore, *,
                              min_claims: int = 2) -> list[dict[str, Any]]:
    """Candidate theme clusters: current claims grouped by their subject
    entity's connected component under ACTIVE typed relations.

    A candidate is a structural grouping, not a theme — the caller decides
    which become state.
    """
    states = store.claim_states()
    claims = [c for c in store.current_claims().values()
              if states.get(c["claim_id"], {}).get("state", "CURRENT") == "CURRENT"]
    if not claims:
        return []
    # union-find over subject objects, joined by ACTIVE relations
    parents: dict[str, str] = {}

    def find(node: str) -> str:
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for claim in claims:
        find(claim["subject_object_id"])
        if claim["object_object_id"]:
            union(claim["subject_object_id"], claim["object_object_id"])
    for relation in store.latest_by_id("relationship_version",
                                       "relationship_id").values():
        if relation["status"] == "ACTIVE":
            union(relation["source_object_id"], relation["target_object_id"])

    components: dict[str, list[Mapping[str, Any]]] = {}
    for claim in claims:
        components.setdefault(find(claim["subject_object_id"]), []).append(claim)

    objects = store.latest_by_id("object_version", "object_id")
    activities = store.records_of("activity")
    candidates = []
    for root, member_claims in sorted(components.items()):
        if len(member_claims) < min_claims:
            continue
        entity_ids = tuple(sorted({c["subject_object_id"] for c in member_claims}
                                  | {c["object_object_id"] for c in member_claims
                                     if c["object_object_id"]}))
        entity_set = set(entity_ids)
        event_ids = tuple(sorted({a["activity_id"] for a in activities
                                  if entity_set & set(a["subject_ids"])}))
        labels = []
        for entity_id in entity_ids:
            version = objects.get(entity_id)
            if version:
                name = version.get("attributes", {}).get("name") \
                    or (version.get("labels") or ("",))[0]
                if name:
                    labels.append(name)
        predicates = sorted({c["predicate"] for c in member_claims})
        title = (f"{labels[0] if labels else entity_ids[0][:16]}: "
                 f"{', '.join(predicates[:4])}")
        candidates.append({
            "candidate_id": digest_id("themecand", root),
            "title": title,
            "entity_ids": entity_ids,
            "event_ids": event_ids,
            "entity_labels": tuple(labels[:8]),
            "claim_ids": tuple(sorted(c["claim_id"] for c in member_claims)),
            "predicates": tuple(predicates),
            "component_root": root,
            "method": "world-graph connected component over CURRENT claims "
                      "and ACTIVE typed relations (deterministic)",
        })
    return candidates


def explain_theme(store: AnalyticStore, theme_id: str) -> dict[str, Any]:
    """Why this theme exists: membership, families, contradiction, descent."""
    from .basis import describe_descent
    theme = store.current_themes().get(theme_id)
    if theme is None:
        return {"theme_id": theme_id, "status": "UNKNOWN_THEME"}
    basis = theme["basis"]
    claims = store.current_claims()
    return {
        "theme_id": theme_id,
        "title": theme["title"],
        "status": theme["status"],
        "authority": theme["authority"],
        "why": [claims.get(claim_id, {}).get("statement", claim_id)
                for claim_id in basis["supporting_claim_ids"]],
        "against": [claims.get(claim_id, {}).get("statement", claim_id)
                    for claim_id in basis["contradicting_claim_ids"]],
        "source_basis": {
            "manifestations": basis["manifestation_count"],
            "sources": basis["source_count"],
            "independent_origin_families": len(basis["origin_families"]),
            "caveat": basis["note"],
        },
        "temporal": {"emerged": theme["valid_from"],
                     "evidence_span": (basis["earliest_time"], basis["latest_time"])},
        "degraded_support": basis["degraded_claim_count"],
        "history": list(theme["history"]),
        "transitions": [
            {"type": t["transition_type"], "detail": t["detail"],
             "at": t["recorded_time"], "caused_by": t["caused_by"]}
            for t in store.transitions_for(theme_id)],
        "descent": [describe_descent(store, claim_id)
                    for claim_id in basis["supporting_claim_ids"][:5]],
    }

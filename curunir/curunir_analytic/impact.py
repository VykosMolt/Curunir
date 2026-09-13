"""Impact and exposure: typed paths from a world change to a mission objective.

A path is a connected chain of typed, evidence-bearing edges, and adjacency
alone is never causality. Its authority is its weakest edge, and a response
option is accepted only by a recorded human act.
"""
from __future__ import annotations

from typing import Any, Mapping

from argus.source_intelligence.models import digest_id

from .contracts import (AssumptionRecord, ImpactEdge, ImpactPath, MissionObjective,
                        ResponseOption, weakest_authority)
from .store import AnalyticStore
from .substrate import (AnalyticContext, DependencyIndex, append_version,
                        ensure_transition, record_transition)

# The relation types exposure propagation may walk.
_PROPAGATION_RELATIONS = ("DEPENDS_ON", "SUPPLIES", "OWNS", "OPERATES",
                          "HOLDS_ROLE", "PUBLISHED_BY", "SUCCESSOR_OF")


# Objectives


def create_objective(ctx: AnalyticContext, *, mission_context: str, statement: str,
                     priority: str = "MEDIUM", time_horizon: str = "",
                     depends_on: tuple[tuple[str, str], ...] = (),
                     assumption_ids: tuple[str, ...] = (),
                     caused_by: str = "") -> dict[str, Any]:
    store = ctx.store
    objective_id = digest_id("objective", mission_context, statement)
    existing = store.current_objectives().get(objective_id)
    if existing is not None:
        ensure_transition(ctx, subject_kind="mission_objective",
                          subject_id=objective_id, transition_type="CREATED",
                          detail=f"objective: {existing['statement'][:160]}",
                          caused_by=caused_by or objective_id,
                          to_status=existing["status"])
        return existing
    record = MissionObjective(
        objective_id=objective_id, version=1, mission_context=mission_context,
        statement=statement, status="ACTIVE", priority=priority,
        time_horizon=time_horizon, depends_on=depends_on,
        assumption_ids=assumption_ids, change_reason="",
        history=("CREATED",), recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="mission_objective", subject_id=objective_id,
                      transition_type="CREATED",
                      detail=f"objective: {statement[:160]}",
                      caused_by=caused_by or objective_id, to_status="ACTIVE")
    return appended


def _reappend_objective(ctx: AnalyticContext, objective: Mapping[str, Any],
                        updates: dict[str, Any], change_reason: str,
                        history_note: str) -> dict[str, Any]:
    merged = {k: v for k, v in objective.items() if k != "record_type"}
    merged.update(updates)
    merged["version"] = ctx.store.next_analytic_version(
        "mission_objective", objective["objective_id"])
    merged["change_reason"] = change_reason
    merged["history"] = tuple(objective["history"]) + (history_note,)
    merged["recorded_time"] = ctx.now_fn()
    merged["marking"] = ctx.marking
    merged["depends_on"] = tuple(tuple(pair) for pair in merged["depends_on"])
    merged["assumption_ids"] = tuple(merged["assumption_ids"])
    record = MissionObjective(**merged)
    return append_version(ctx, record)


def mark_objective_exposed(ctx: AnalyticContext, objective_id: str, *,
                           detail: str, caused_by: str) -> dict[str, Any]:
    store = ctx.store
    objective = store.current_objectives().get(objective_id)
    if objective is None:
        raise ValueError(f"unknown objective: {objective_id}")
    if objective["status"] == "EXPOSED":
        record_transition(ctx, subject_kind="mission_objective",
                          subject_id=objective_id, transition_type="EXPOSED",
                          detail=detail[:300], caused_by=caused_by,
                          from_status="EXPOSED", to_status="EXPOSED")
        return objective
    updated = _reappend_objective(ctx, objective, {"status": "EXPOSED"},
                                  change_reason=detail[:200],
                                  history_note="EXPOSED")
    record_transition(ctx, subject_kind="mission_objective", subject_id=objective_id,
                      transition_type="EXPOSED", detail=detail[:300],
                      caused_by=caused_by,
                      from_status=objective["status"], to_status="EXPOSED")
    return updated


# Assumptions


def record_assumption(ctx: AnalyticContext, *, statement: str,
                      supporting_claim_ids: tuple[str, ...] = (),
                      objective_ids: tuple[str, ...] = (),
                      caused_by: str = "") -> dict[str, Any]:
    store = ctx.store
    assumption_id = digest_id("assumption", statement)
    existing = store.current_assumptions().get(assumption_id)
    if existing is not None:
        ensure_transition(ctx, subject_kind="analytic_assumption",
                          subject_id=assumption_id, transition_type="HELD",
                          detail=f"assumption held: {existing['statement'][:160]}",
                          caused_by=caused_by or assumption_id,
                          to_status=existing["status"])
        # Fold in the caller's evidence and objective links, don't discard them.
        new_claims = tuple(c for c in supporting_claim_ids
                           if c not in existing["supporting_claim_ids"])
        new_objectives = tuple(o for o in objective_ids
                               if o not in existing["objective_ids"])
        if new_claims or new_objectives:
            merged = {k: v for k, v in existing.items() if k != "record_type"}
            merged.update(
                version=store.next_analytic_version("analytic_assumption",
                                                    assumption_id),
                supporting_claim_ids=tuple(existing["supporting_claim_ids"])
                + new_claims,
                objective_ids=tuple(existing["objective_ids"]) + new_objectives,
                change_reason="creation over an existing assumption folds the "
                              "new evidence/links",
                history=tuple(existing["history"]) + ("CREATION_FOLD",),
                recorded_time=ctx.now_fn(), marking=ctx.marking)
            merged["contradicting_claim_ids"] = tuple(merged["contradicting_claim_ids"])
            appended = append_version(ctx, AssumptionRecord(**merged))
            record_transition(ctx, subject_kind="analytic_assumption",
                              subject_id=assumption_id,
                              transition_type="EVIDENCE_UPDATED",
                              detail=f"creation fold: +{len(new_claims)} claim(s), "
                                     f"+{len(new_objectives)} objective link(s)",
                              caused_by=digest_id("fold", assumption_id,
                                                  *sorted(new_claims + new_objectives)),
                              evidence_refs=new_claims[:5])
            return appended
        return existing
    record = AssumptionRecord(
        assumption_id=assumption_id, version=1, statement=statement,
        status="HELD", supporting_claim_ids=supporting_claim_ids,
        contradicting_claim_ids=(), objective_ids=objective_ids,
        caused_by="", change_reason="", history=("HELD",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="analytic_assumption", subject_id=assumption_id,
                      transition_type="HELD",
                      detail=f"assumption held: {statement[:160]}",
                      caused_by=caused_by or assumption_id,
                      evidence_refs=supporting_claim_ids[:5], to_status="HELD")
    return appended


def question_assumption(ctx: AnalyticContext, assumption_id: str, *,
                        caused_by: str, reason: str) -> dict[str, Any]:
    """Mark an assumption UNCERTAIN: machine-detectable, unlike INVALIDATED,
    which needs an explicit act carrying contradicting evidence.
    """
    store = ctx.store
    assumption = store.current_assumptions().get(assumption_id)
    if assumption is None:
        raise ValueError(f"unknown assumption: {assumption_id}")
    if assumption["status"] == "UNCERTAIN":
        # Complete a missing transition; append nothing.
        record_transition(ctx, subject_kind="analytic_assumption",
                          subject_id=assumption_id, transition_type="QUESTIONED",
                          detail=reason[:300], caused_by=caused_by,
                          from_status="HELD", to_status="UNCERTAIN")
        return assumption
    if assumption["status"] != "HELD":
        return assumption
    merged = {k: v for k, v in assumption.items() if k != "record_type"}
    merged.update(
        version=store.next_analytic_version("analytic_assumption", assumption_id),
        status="UNCERTAIN", caused_by=caused_by, change_reason=reason,
        history=tuple(assumption["history"]) + (f"QUESTIONED:{reason[:60]}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    for key in ("supporting_claim_ids", "contradicting_claim_ids", "objective_ids"):
        merged[key] = tuple(merged[key])
    record = AssumptionRecord(**merged)
    appended = append_version(ctx, record)
    record_transition(ctx, subject_kind="analytic_assumption", subject_id=assumption_id,
                      transition_type="QUESTIONED", detail=reason[:300],
                      caused_by=caused_by, from_status="HELD", to_status="UNCERTAIN")
    return appended


def invalidate_assumption(ctx: AnalyticContext, assumption_id: str, *,
                          contradicting_claim_ids: tuple[str, ...],
                          caused_by: str, reason: str) -> dict[str, Any]:
    """Invalidate an assumption and mark everything resting on it.

    Every dependent path goes STALE and every affected objective EXPOSED.
    """
    store = ctx.store
    assumption = store.current_assumptions().get(assumption_id)
    if assumption is None:
        raise ValueError(f"unknown assumption: {assumption_id}")
    # The marking pass below is idempotent, so a re-run completes an
    # interrupted call.
    if assumption["status"] == "INVALIDATED":
        appended = assumption
    else:
        merged = {k: v for k, v in assumption.items() if k != "record_type"}
        merged.update(
            version=store.next_analytic_version("analytic_assumption", assumption_id),
            status="INVALIDATED",
            contradicting_claim_ids=tuple(dict.fromkeys(
                tuple(assumption["contradicting_claim_ids"]) + contradicting_claim_ids)),
            caused_by=caused_by, change_reason=reason,
            history=tuple(assumption["history"]) + (f"INVALIDATED:{reason[:60]}",),
            recorded_time=ctx.now_fn(), marking=ctx.marking)
        for key in ("supporting_claim_ids", "objective_ids"):
            merged[key] = tuple(merged[key])
        record = AssumptionRecord(**merged)
        appended = append_version(ctx, record)
    record_transition(ctx, subject_kind="analytic_assumption", subject_id=assumption_id,
                      transition_type="INVALIDATED", detail=reason[:300],
                      caused_by=caused_by, evidence_refs=contradicting_claim_ids[:5],
                      from_status="HELD", to_status="INVALIDATED")
    index = DependencyIndex(store)
    for kind, ref in sorted(index.affected_by(assumption_ids=[assumption_id])):
        if kind == "impact_path":
            path = store.current_impact_paths().get(ref)
            if path and path["status"] not in ("STALE", "INVALIDATED"):
                _restate_path(ctx, path, status="STALE",
                              change_reason=f"assumption invalidated: {reason[:120]}",
                              history_note=f"ASSUMPTION_INVALIDATED:{assumption_id[:18]}")
            record_transition(ctx, subject_kind="impact_path", subject_id=ref,
                              transition_type="ASSUMPTION_INVALIDATED",
                              detail=f"assumption {assumption['statement'][:120]!r} "
                                     f"invalidated: {reason[:150]}",
                              caused_by=caused_by,
                              evidence_refs=contradicting_claim_ids[:5],
                              to_status="STALE")
            if path:
                mark_objective_exposed(
                    ctx, path["objective_id"],
                    detail=f"impact path {ref[:18]} rests on invalidated assumption: "
                           f"{assumption['statement'][:140]}",
                    caused_by=f"{caused_by}:{ref[:18]}")
        elif kind == "mission_objective":
            mark_objective_exposed(
                ctx, ref,
                detail=f"objective assumption invalidated: "
                       f"{assumption['statement'][:160]}",
                caused_by=f"{caused_by}:{assumption_id[:18]}")
    return appended


# Impact paths


def edge_chain_fingerprint(edges) -> str:
    """Ordered fingerprint of an edge chain's content: what a candidate binds
    and a materialization must match, since an edge id is only a label.
    """
    fingerprints = []
    for edge in edges:
        data = edge if isinstance(edge, Mapping) else edge.to_record()
        fingerprints.append(digest_id(
            "edgefp", data["from_kind"], data["from_id"], data["to_kind"],
            data["to_id"], data["edge_kind"], data["effect_order"],
            data["authority"], ",".join(data["basis_ids"]),
            ",".join(data["assumption_ids"]), data["note"][:300]))
    return ">".join(fingerprints)


def build_path(ctx: AnalyticContext, *, objective_id: str, summary: str,
               edges: tuple[ImpactEdge, ...],
               provenance_kind: str = "RULE", inference_id: str = "",
               proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    """Construct an impact path; the chain must end at the stated objective."""
    store = ctx.store
    if store.current_objectives().get(objective_id) is None:
        raise ValueError(f"unknown objective: {objective_id}")
    if not edges or edges[-1].to_kind != "mission_objective" \
            or edges[-1].to_id != objective_id:
        raise ValueError("an impact path must terminate at its objective")
    path_id = digest_id("impath", objective_id,
                        *(edge.edge_id for edge in edges))
    existing = store.current_impact_paths().get(path_id)
    if existing is not None:
        # Edge ids are caller-chosen labels, so two chains can land on one
        # path_id. The same chain reconciles; a different one is a different
        # path and must not be silently dropped.
        if edge_chain_fingerprint(existing["edges"]) \
                != edge_chain_fingerprint(edges):
            raise ValueError(
                f"impact path {path_id[:24]} already exists with a different "
                "edge chain: a re-call reconciles the same chain, and a "
                "different chain whose edge ids collide is a different path")
        # A re-call completes its own accepted materialization; the
        # consumption check is not re-entered.
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                           != existing.get("proposal_id")):
            raise ValueError(
                "an existing impact path cannot be modified under model "
                "provenance with a different proposal")
        ensure_transition(ctx, subject_kind="impact_path", subject_id=path_id,
                          transition_type="CONSTRUCTED",
                          detail=f"{existing['summary'][:140]} "
                                 f"({len(existing['edges'])} edges)",
                          caused_by=caused_by or path_id,
                          to_status=existing["status"])
        return existing
    if provenance_kind == "MODEL":
        from .substrate import require_accepted_candidate
        require_accepted_candidate(
            store, inference_id=inference_id, proposal_id=proposal_id,
            target_kind="impact_path",
            materialized={"objective_id": objective_id, "summary": summary,
                          # The human accepted the chain's content in order,
                          # not its edge labels.
                          "edge_chain": edge_chain_fingerprint(edges)})
    # Every cited id must resolve, or refresh could never find a basis to
    # degrade and the path would keep its authority forever.
    known_basis: set[str] = set(store.current_claims())
    known_basis.update(r["relationship_id"]
                       for r in store.records_of("relationship_version"))
    known_basis.update(a["activity_id"] for a in store.records_of("activity"))
    known_basis.update(store.current_objectives())
    known_assumptions = set(store.current_assumptions())
    for edge in edges:
        phantom = [b for b in edge.basis_ids if b not in known_basis]
        if phantom:
            raise ValueError(
                f"edge {edge.edge_id[:18]} cites {len(phantom)} basis id(s) that "
                f"resolve to no claim/relationship/event/objective in the log: "
                f"{phantom[0][:40]!r} — phantom ids are not evidence")
        missing_assumptions = [a for a in edge.assumption_ids
                               if a not in known_assumptions]
        if missing_assumptions:
            raise ValueError(
                f"edge {edge.edge_id[:18]} cites unknown assumption "
                f"{missing_assumptions[0][:40]!r}")
    assumption_ids = tuple(dict.fromkeys(
        assumption_id for edge in edges for assumption_id in edge.assumption_ids))
    authority = weakest_authority(edge.authority for edge in edges)
    uncertain = [edge for edge in edges
                 if edge.authority not in ("OBSERVED", "DERIVED") or edge.assumption_ids]
    uncertainty_note = ""
    if uncertain:
        reasons = []
        for edge in uncertain:
            if edge.authority not in ("OBSERVED", "DERIVED"):
                reasons.append(f"edge {edge.from_id[:12]}→{edge.to_id[:12]} is "
                               f"{edge.authority}")
            if edge.assumption_ids:
                reasons.append(f"edge {edge.from_id[:12]}→{edge.to_id[:12]} rests on "
                               f"{len(edge.assumption_ids)} assumption(s)")
        uncertainty_note = "; ".join(reasons)[:400]
    record = ImpactPath(
        path_id=path_id, version=1, objective_id=objective_id, summary=summary,
        edges=edges, status="ASSESSED", path_authority=authority,
        uncertainty_note=uncertainty_note, assumption_ids=assumption_ids,
        provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "",
        change_reason="",
        history=(f"CONSTRUCTED:{provenance_kind}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    orders = [edge.effect_order for edge in edges]
    ensure_transition(ctx, subject_kind="impact_path", subject_id=path_id,
                      transition_type="CONSTRUCTED",
                      detail=f"{summary[:140]} ({len(edges)} edges, "
                             f"authority {authority}, orders {'/'.join(orders)})",
                      caused_by=caused_by or path_id,
                      evidence_refs=tuple(b for edge in edges
                                          for b in edge.basis_ids)[:8],
                      to_status="ASSESSED")
    return appended


def _restate_path(ctx: AnalyticContext, path: Mapping[str, Any], *, status: str,
                  change_reason: str, history_note: str) -> dict[str, Any]:
    merged = {k: v for k, v in path.items() if k != "record_type"}
    merged.update(
        version=ctx.store.next_analytic_version("impact_path", path["path_id"]),
        status=status, change_reason=change_reason,
        history=tuple(path["history"]) + (history_note,),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    merged["assumption_ids"] = tuple(merged["assumption_ids"])
    edges = []
    for edge in merged["edges"]:
        data = {k: v for k, v in edge.items() if k != "record_type"}
        for key in ("basis_ids", "assumption_ids"):
            data[key] = tuple(data[key])
        edges.append(ImpactEdge(**data))
    merged["edges"] = tuple(edges)
    record = ImpactPath(**merged)
    return append_version(ctx, record)


def refresh_path(ctx: AnalyticContext, path_id: str, *, caused_by: str) -> dict[str, Any]:
    """Recheck a path against live claim state; degraded evidence marks it STALE."""
    store = ctx.store
    path = store.current_impact_paths().get(path_id)
    if path is None:
        raise ValueError(f"unknown impact path: {path_id}")
    if path["status"] in ("INVALIDATED", "RESOLVED"):
        return path
    from .basis import DEGRADED_CLAIM_STATES
    claims = store.current_claims()
    states = store.claim_states()
    degraded = []
    for edge in path["edges"]:
        for basis_id in edge["basis_ids"]:
            if basis_id not in claims:
                continue
            state = states.get(basis_id, {}).get("state", "CURRENT")
            if state in DEGRADED_CLAIM_STATES:
                degraded.append((edge["edge_id"], basis_id, state))
    if not degraded:
        return path
    detail = "; ".join(f"edge {edge_id[:12]}: claim {claim_id[:16]} is {state}"
                       for edge_id, claim_id, state in degraded)[:300]
    # Identified by the degraded evidence, not the change that triggered the
    # refresh, so an unrelated later change re-runs as a no-op while new
    # degradation is a new finding.
    finding = digest_id("stale-finding", path_id,
                        *sorted(f"{claim_id}:{state}"
                                for _, claim_id, state in degraded))
    updated = path
    if path["status"] != "STALE":
        updated = _restate_path(ctx, path, status="STALE",
                                change_reason=f"evidence degraded: {detail[:160]}",
                                history_note="STALE:evidence")
    record_transition(ctx, subject_kind="impact_path", subject_id=path_id,
                      transition_type="STALE", detail=detail, caused_by=finding,
                      evidence_refs=tuple(claim_id for _, claim_id, _ in degraded)[:5],
                      from_status=path["status"], to_status="STALE")
    mark_objective_exposed(ctx, path["objective_id"],
                           detail=f"impact path {path_id[:18]} basis degraded: "
                                  f"{detail[:160]}",
                           caused_by=f"{finding}:{path_id[:18]}")
    return updated


def suggest_path_edges(store: AnalyticStore, *, activity_id: str,
                       objective_id: str, max_hops: int = 3
                       ) -> tuple[ImpactEdge, ...] | None:
    """Suggest a typed edge chain from an event to an objective, walking only
    ACTIVE evidence-stated relations and declared dependencies. None if there is
    no typed path.
    """
    activity = next((a for a in store.records_of("activity")
                     if a["activity_id"] == activity_id), None)
    objective = store.current_objectives().get(objective_id)
    if activity is None or objective is None:
        return None
    dependency_objects = {ref for kind, ref in objective["depends_on"]
                          if kind == "object"}
    if not dependency_objects:
        return None
    adjacency: dict[str, list[Mapping[str, Any]]] = {}
    for relation in store.latest_by_id("relationship_version",
                                       "relationship_id").values():
        if relation["status"] == "ACTIVE" \
                and relation["relation_type"] in _PROPAGATION_RELATIONS:
            adjacency.setdefault(relation["source_object_id"], []).append(relation)
            adjacency.setdefault(relation["target_object_id"], []).append(relation)

    subjects = list(activity.get("subject_ids") or ())
    for subject in subjects:
        frontier = [(subject, [])]
        seen = {subject}
        while frontier:
            node, trail = frontier.pop(0)
            if node in dependency_objects:
                edges = [ImpactEdge(
                    edge_id=digest_id("imedge", activity_id, subject),
                    from_kind="activity", from_id=activity_id,
                    to_kind="object", to_id=subject,
                    edge_kind="EVENT_EFFECT", effect_order="DIRECT",
                    authority="OBSERVED",
                    note="the event's evidence names this entity as its subject",
                    basis_ids=(activity_id,), assumption_ids=())]
                for relation in trail:
                    edges.append(ImpactEdge(
                        edge_id=digest_id("imedge", relation["relationship_id"],
                                          activity_id),
                        from_kind="object", from_id=edges[-1].to_id,
                        to_kind="object",
                        to_id=relation["target_object_id"]
                        if relation["source_object_id"] == edges[-1].to_id
                        else relation["source_object_id"],
                        edge_kind="TYPED_RELATION", effect_order="SECOND_ORDER",
                        authority="DERIVED",
                        note=f"exposure propagated along evidence-stated "
                             f"{relation['relation_type']}",
                        basis_ids=(relation["relationship_id"],), assumption_ids=()))
                edges.append(ImpactEdge(
                    edge_id=digest_id("imedge", objective_id, node),
                    from_kind="object", from_id=node,
                    to_kind="mission_objective", to_id=objective_id,
                    edge_kind="DEPENDENCY",
                    effect_order="SECOND_ORDER" if trail else "DIRECT",
                    authority="DERIVED",
                    note="the objective declares this dependency",
                    basis_ids=(objective_id,), assumption_ids=()))
                return tuple(edges)
            if len(trail) >= max_hops:
                continue
            for relation in adjacency.get(node, ()):
                other = relation["target_object_id"] \
                    if relation["source_object_id"] == node \
                    else relation["source_object_id"]
                if other not in seen:
                    seen.add(other)
                    frontier.append((other, trail + [relation]))
    return None


# Response options


def propose_response_option(ctx: AnalyticContext, *, objective_id: str, path_id: str,
                            description: str, prerequisites: tuple[str, ...] = (),
                            tradeoffs: tuple[str, ...] = (),
                            claim_ids: tuple[str, ...] = (),
                            uncertainty_note: str = "",
                            provenance_kind: str = "ANALYST", inference_id: str = "",
                            proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    store = ctx.store
    option_id = digest_id("respopt", objective_id, path_id, description)
    existing = store.current_analytics("response_option").get(option_id)
    if existing is not None:
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                           != existing.get("proposal_id")):
            raise ValueError(
                "an existing response option cannot be modified under model "
                "provenance with a different proposal")
        ensure_transition(ctx, subject_kind="response_option", subject_id=option_id,
                          transition_type="PROPOSED",
                          detail=f"response option proposed: "
                                 f"{existing['description'][:160]}",
                          caused_by=caused_by or option_id,
                          to_status=existing["status"])
        return existing
    if provenance_kind == "MODEL":
        from .substrate import require_accepted_candidate
        require_accepted_candidate(
            store, inference_id=inference_id, proposal_id=proposal_id,
            target_kind="response_option",
            materialized={"objective_id": objective_id, "path_id": path_id,
                          "description": description})
    record = ResponseOption(
        option_id=option_id, version=1, objective_id=objective_id, path_id=path_id,
        description=description, prerequisites=prerequisites, tradeoffs=tradeoffs,
        claim_ids=claim_ids, uncertainty_note=uncertainty_note,
        status="PROPOSED", human_actor="",
        provenance_kind=provenance_kind, inference_id=inference_id,
        change_reason="", history=(f"PROPOSED:{provenance_kind}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "")
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="response_option", subject_id=option_id,
                      transition_type="PROPOSED",
                      detail=f"response option proposed: {description[:160]}",
                      caused_by=caused_by or option_id, to_status="PROPOSED")
    return appended


def review_response_option(ctx: AnalyticContext, option_id: str, *, accept: bool,
                           actor_id: str, actor_kind: str, note: str) -> dict[str, Any]:
    """Accept or reject a response option; only a human may accept."""
    if accept and actor_kind != "HUMAN":
        raise ValueError("only a recorded human act can accept a response option")
    store = ctx.store
    option = store.current_analytics("response_option").get(option_id)
    if option is None:
        raise ValueError(f"unknown response option: {option_id}")
    status = "ACCEPTED" if accept else "REJECTED"
    if option["status"] not in ("PROPOSED", "UNDER_REVIEW"):
        if option["status"] == status:
            # Complete a missing transition; append nothing.
            record_transition(ctx, subject_kind="response_option",
                              subject_id=option_id, transition_type=status,
                              detail=note[:300], caused_by=f"analyst:{actor_id}",
                              from_status="PROPOSED", to_status=status)
            return option
        raise ValueError(f"response option {option_id[:24]} is already "
                         f"{option['status']}: a resolved option is not re-decided")
    merged = {k: v for k, v in option.items() if k != "record_type"}
    merged.update(
        version=store.next_analytic_version("response_option", option_id),
        status=status, human_actor=actor_id if accept else option["human_actor"],
        change_reason=note, history=tuple(option["history"]) + (f"{status}:{actor_id}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    for key in ("prerequisites", "tradeoffs", "claim_ids"):
        merged[key] = tuple(merged[key])
    record = ResponseOption(**merged)
    appended = append_version(ctx, record)
    record_transition(ctx, subject_kind="response_option", subject_id=option_id,
                      transition_type=status,
                      detail=note[:300], caused_by=f"analyst:{actor_id}",
                      from_status=option["status"], to_status=status)
    return appended


def explain_path(store: AnalyticStore, path_id: str) -> dict[str, Any]:
    path = store.current_impact_paths().get(path_id)
    if path is None:
        return {"path_id": path_id, "status": "UNKNOWN_PATH"}
    objective = store.current_objectives().get(path["objective_id"], {})
    assumptions = store.current_assumptions()
    return {
        "path_id": path_id,
        "objective": objective.get("statement", path["objective_id"]),
        "objective_status": objective.get("status", "UNKNOWN"),
        "summary": path["summary"],
        "status": path["status"],
        "path_authority": path["path_authority"],
        "uncertainty": path["uncertainty_note"],
        "edges": [
            {"step": index + 1,
             "from": f"{edge['from_kind']}:{edge['from_id'][:24]}",
             "to": f"{edge['to_kind']}:{edge['to_id'][:24]}",
             "kind": edge["edge_kind"], "effect_order": edge["effect_order"],
             "authority": edge["authority"], "note": edge["note"],
             "evidence": list(edge["basis_ids"]),
             "assumptions": [assumptions.get(a, {}).get("statement", a)
                             for a in edge["assumption_ids"]]}
            for index, edge in enumerate(path["edges"])],
        "assumptions": [
            {"statement": assumptions.get(a, {}).get("statement", a),
             "status": assumptions.get(a, {}).get("status", "UNKNOWN")}
            for a in path["assumption_ids"]],
        "history": list(path["history"]),
        "transitions": [
            {"type": t["transition_type"], "detail": t["detail"], "at": t["recorded_time"]}
            for t in store.transitions_for(path_id)],
    }

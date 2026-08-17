"""Provenance descent and ascent as first-class projections.

Descent:  warning → forecast → impact/assumption → claim → observation →
          exact anchor → manifestation → source.
Ascent:   source → manifestations → observations → claims → analytical
          objects → mission consequences.

Every hop resolves only records the context can view; a chain never narrates
hidden state, and a link into a hidden record terminates with an explicit
INACCESSIBLE node rather than pretending the chain ends there naturally.
"""
from __future__ import annotations

from typing import Any

from .projections import MissionProjection, _base_id


def _node(kind: str, record_id: str, label: str, record: dict | None = None,
          **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "id": record_id, "label": label,
            "record": record, **extra}


def _inaccessible(kind: str) -> dict[str, Any]:
    return {"kind": kind, "id": "REDACTED", "label": "not accessible in this context",
            "record": None, "inaccessible": True}


def claim_descent(projection: MissionProjection, claim_id: str) -> dict[str, Any] | None:
    """claim → observations → anchors → manifestations → sources."""
    claim = projection.get("semantic_claim", claim_id)
    if claim is None:
        return None
    observations = []
    for observation_id in claim.get("observation_ids", ()):
        if observation_id == "REDACTED":
            observations.append(_inaccessible("semantic_observation"))
            continue
        observation = projection.get("semantic_observation", observation_id)
        if observation is None:
            observations.append(_inaccessible("semantic_observation"))
            continue
        anchors = []
        for anchor in observation.get("anchors", ()):
            manifestation = projection.get("fabric_manifestation", anchor.get("manifestation_id", ""))
            source = None
            if manifestation is not None:
                source = projection.get("fabric_source_descriptor", manifestation["source_id"]) \
                    or {"source_id": manifestation["source_id"]}
            anchors.append({
                "anchor": anchor,
                "manifestation": manifestation if manifestation is not None
                else _inaccessible("fabric_manifestation"),
                "source": source,
            })
        observations.append(_node(
            "semantic_observation", observation["observation_id"],
            f"{observation['attribute']} = {observation['value'][:80]}",
            observation, anchors=anchors))
    return {
        "claim": _node("semantic_claim", claim["claim_id"],
                       claim["statement"], claim),
        "observations": observations,
        "independent_basis_count": claim.get("independent_basis_count", 0),
        "dependence_group_ids": claim.get("dependence_group_ids", ()),
    }


def descend(projection: MissionProjection, kind: str, record_id: str) -> dict[str, Any] | None:
    """Full descent from any analytical object to source evidence.

    Returns a chain of layers; each layer states what it is (projection,
    authored judgment, proposition, extraction fact, exact anchor, custody,
    source) so inference is never rendered as observation."""
    chain: list[dict[str, Any]] = []
    claim_ids: list[str] = []

    if kind == "strategic_warning":
        warning = projection.get("strategic_warning", record_id)
        if warning is None:
            return None
        chain.append(_node("strategic_warning", record_id,
                           f"warning tier {warning['tier']} ({warning['tier_rule_id']})",
                           warning, layer="RULE_PROJECTION",
                           component_basis=warning.get("component_basis", ())))
        objective = projection.get("mission_objective", warning["objective_id"]) \
            if warning["objective_id"] != "REDACTED" else None
        if objective:
            chain.append(_node("mission_objective", objective["objective_id"],
                               objective.get("title", objective["objective_id"]),
                               objective, layer="OBJECTIVE"))
        for path_id in warning.get("impact_path_ids", ()):
            path = projection.get("impact_path", path_id) if path_id != "REDACTED" else None
            chain.append(_node("impact_path", path_id,
                               path.get("summary", path_id) if path else "not accessible",
                               path, layer="IMPACT_PATH",
                               inaccessible=path is None))
        kind, record_id = "analytic_forecast", warning["forecast_id"]
        if record_id == "REDACTED":
            chain.append(_inaccessible("analytic_forecast"))
            return {"chain": chain, "claims": []}

    if kind == "analytic_forecast":
        forecast = projection.get("analytic_forecast", record_id)
        if forecast is None:
            return None
        chain.append(_node("analytic_forecast", record_id,
                           f"p={forecast['probability']:.2f}: {forecast['question']}",
                           forecast, layer="AUTHORED_JUDGMENT",
                           author=forecast["author"],
                           probability_basis=forecast["probability_basis"]))
        for assumption_id in forecast.get("assumption_ids", ()):
            assumption = projection.get("analytic_assumption", assumption_id) \
                if assumption_id != "REDACTED" else None
            chain.append(_node("analytic_assumption", assumption_id,
                               assumption.get("statement", "not accessible") if assumption
                               else "not accessible", assumption,
                               layer="ASSUMPTION", inaccessible=assumption is None))
        basis = forecast.get("basis", {}) or {}
        claim_ids = list(basis.get("supporting_claim_ids", ())) \
            + list(basis.get("contradicting_claim_ids", ()))
    elif kind in ("analytic_theme", "analytic_narrative", "hypothesis",
                  "impact_path", "stakeholder_assessment", "influence_assertion"):
        record = projection.get(kind, record_id)
        if record is None:
            return None
        label = record.get("title") or record.get("statement") or record.get("summary") or record_id
        chain.append(_node(kind, record_id, str(label)[:120], record,
                           layer="ANALYTICAL_OBJECT",
                           authority=record.get("authority", "")))
        claim_ids = list(record.get("member_claim_ids", ())
                         or record.get("supporting_claim_ids", ())
                         or record.get("claim_ids", ())
                         or tuple(record.get("basis", {}).get("supporting_claim_ids", ()))
                         + tuple(record.get("basis", {}).get("contradicting_claim_ids", ())))
        if kind == "hypothesis":
            claim_ids += list(record.get("contradicting_claim_ids", ()))
    elif kind == "semantic_claim":
        claim_ids = [record_id]
    else:
        return None

    claims = []
    for claim_id in claim_ids:
        if claim_id == "REDACTED":
            claims.append(_inaccessible("semantic_claim"))
            continue
        descent = claim_descent(projection, claim_id)
        claims.append(descent if descent is not None else _inaccessible("semantic_claim"))
    return {"chain": chain, "claims": claims}


def ascend(projection: MissionProjection, kind: str, record_id: str) -> dict[str, Any] | None:
    """From evidence upward: which observations, claims and analytical
    objects rest on this source/manifestation/claim."""
    manifestation_ids: set[str] = set()
    if kind == "fabric_source_descriptor":
        source = projection.get("fabric_source_descriptor", record_id)
        if source is None:
            return None
        manifestation_ids = {m["manifestation_id"] for m in projection.family("fabric_manifestation")
                             if m["source_id"] == record_id}
        root = _node("fabric_source_descriptor", record_id,
                     source.get("title") or record_id, source)
    elif kind == "fabric_manifestation":
        manifestation = projection.get("fabric_manifestation", record_id)
        if manifestation is None:
            return None
        manifestation_ids = {record_id}
        root = _node("fabric_manifestation", record_id,
                     manifestation.get("final_url") or record_id, manifestation)
    elif kind == "semantic_claim":
        claim = projection.get("semantic_claim", record_id)
        if claim is None:
            return None
        root = _node("semantic_claim", record_id, claim["statement"], claim)
        return {"root": root, "observations": [], "claims": [],
                "dependents": _analytic_dependents(projection, {record_id})}
    else:
        return None

    observations = [o for o in projection.family("semantic_observation")
                    if o["manifestation_id"] in manifestation_ids]
    observation_ids = {o["observation_id"] for o in observations}
    claims = [c for c in projection.family("semantic_claim")
              if set(c.get("observation_ids", ())) & observation_ids]
    claim_ids = {c["claim_id"] for c in claims}
    return {
        "root": root,
        "observations": [{"observation_id": o["observation_id"],
                          "attribute": o["attribute"], "value": o["value"][:120]}
                         for o in observations],
        "claims": [{"claim_id": c["claim_id"], "statement": c["statement"],
                    "epistemic_state": c["epistemic_state"]} for c in claims],
        "dependents": _analytic_dependents(projection, claim_ids),
    }


def _analytic_dependents(projection: MissionProjection, claim_ids: set[str]) -> list[dict]:
    """Analytical objects whose basis intersects the given claims."""
    dependents = []

    def _basis_claims(record: dict) -> set[str]:
        refs = set(record.get("member_claim_ids", ())) \
            | set(record.get("supporting_claim_ids", ())) \
            | set(record.get("contradicting_claim_ids", ())) \
            | set(record.get("claim_ids", ())) \
            | set(record.get("basis", {}).get("supporting_claim_ids", ()) if isinstance(record.get("basis"), dict) else ()) \
            | set(record.get("basis", {}).get("contradicting_claim_ids", ()) if isinstance(record.get("basis"), dict) else ())
        return {r for r in refs if r != "REDACTED"}

    families = (("analytic_theme", "theme_id", "title"),
                ("analytic_narrative", "narrative_id", "title"),
                ("hypothesis", "hypothesis_id", "statement"),
                ("stakeholder_assessment", "assessment_id", "entity_label"),
                ("impact_path", "path_id", "summary"),
                ("analytic_forecast", "forecast_id", "question"),
                ("analytic_assumption", "assumption_id", "statement"))
    for record_type, id_field, label_field in families:
        for record in projection.family(record_type):
            if _basis_claims(record) & claim_ids:
                dependents.append({"kind": record_type, "id": record[id_field],
                                   "label": str(record.get(label_field, record[id_field]))[:120],
                                   "status": record.get("status", "")})
    forecast_ids = {d["id"] for d in dependents if d["kind"] == "analytic_forecast"}
    for record in projection.family("strategic_warning"):
        if record["forecast_id"] in forecast_ids:
            dependents.append({"kind": "strategic_warning", "id": record["warning_id"],
                               "label": f"warning tier {record['tier']}",
                               "status": record.get("status", "")})
    return dependents


def evidence_view(projection: MissionProjection, manifestation_id: str) -> dict[str, Any] | None:
    """The evidence inspector: custody metadata, native/normalized payloads,
    every anchor into this manifestation, and what depends on it."""
    manifestation = projection.get("fabric_manifestation", manifestation_id)
    if manifestation is None:
        return None
    source = projection.get("fabric_source_descriptor", manifestation["source_id"])
    observations = [o for o in projection.family("semantic_observation")
                    if o["manifestation_id"] == manifestation_id]
    anchors = []
    for observation in observations:
        for anchor in observation.get("anchors", ()):
            anchors.append({**anchor, "observation_id": observation["observation_id"],
                            "attribute": observation["attribute"],
                            "value": observation["value"][:200]})
    upward = ascend(projection, "fabric_manifestation", manifestation_id) or {}

    def _load_verified(sha: str, custody_path: str = "") -> dict:
        """Payload bytes, served ONLY when their sha256 matches the recorded
        custody hash — a moved or tampered file renders as unavailable, never
        as evidence. Custody-path fallback is confined to the mission root."""
        import hashlib
        from pathlib import Path
        mission_root = Path(projection.store.root).resolve().parent
        candidates = []
        from curunir_operational.store import StoreError
        try:
            candidates.append(projection.store.get_payload(sha))
        except (KeyError, FileNotFoundError, OSError, StoreError):
            # get_payload now fails LOUD on a corrupt/torn payload (review A-F2);
            # this designed graceful path catches it and falls back to the VERIFIED
            # custody copy, exactly as for a missing file — a corrupt payload must
            # render as "unavailable", never 500 the evidence view (review B-2)
            pass
        # canonical content-addressed custody location under the mission
        # root; the recorded path is a last resort, and only when it resolves
        # INSIDE the mission root as an ordinary bounded-size file — a fifo,
        # device node or symlink out of the root never blocks the request
        MAX_PAYLOAD = 64 * 1024 * 1024
        canonical = mission_root / "custody" / "sha256" / sha[:2] / sha[2:4] / sha
        recorded = None
        if custody_path:
            resolved = (mission_root / custody_path).resolve() \
                if not Path(custody_path).is_absolute() \
                else Path(custody_path).resolve()
            if resolved.is_relative_to(mission_root):
                recorded = resolved
        for path in (canonical, recorded):
            if path is None:
                continue
            try:
                stat = path.stat()
                if not path.is_file() or stat.st_size > MAX_PAYLOAD:
                    continue
                candidates.append(path.read_bytes())
            except OSError:
                continue
        # the hash gate is the actual boundary: only bytes whose sha256
        # equals the recorded custody hash are ever served, so no probe of
        # any path can disclose foreign content
        raw = next((c for c in candidates
                    if hashlib.sha256(c).hexdigest() == sha), None)
        if raw is None:
            return {"sha256": sha, "bytes": None, "truncated": False,
                    "text": None, "unavailable": True}
        text = raw.decode("utf-8", errors="replace")
        return {"sha256": sha, "bytes": len(raw),
                "truncated": len(text) > 200_000, "text": text[:200_000]}

    payload = None
    content_sha = manifestation.get("content_sha256", "")
    if content_sha:
        payload = _load_verified(content_sha,
                                 manifestation.get("content_store_path", ""))
    # anchors' offsets are exact only within the payload named by their
    # normalized_sha256 — group them so highlights never land on other bytes
    normalized_payloads = []
    by_sha: dict[str, list] = {}
    for a in anchors:
        sha = a.get("normalized_sha256")
        if sha:
            by_sha.setdefault(sha, []).append(a)
    for sha in sorted(by_sha):
        loaded = _load_verified(sha)
        loaded["anchors"] = by_sha[sha]
        normalized_payloads.append(loaded)
    return {
        "manifestation": manifestation,
        "source": source,
        "payload": payload,
        "normalized_payloads": normalized_payloads,
        "anchors": anchors,
        "observations": [{"observation_id": o["observation_id"], "attribute": o["attribute"],
                          "value": o["value"][:200], "language": o.get("language", ""),
                          "producer_kind": o.get("producer_kind", "")}
                         for o in observations],
        "claims": upward.get("claims", []),
        "dependents": upward.get("dependents", []),
        "annotations": projection.annotations_for(manifestation_id),
    }

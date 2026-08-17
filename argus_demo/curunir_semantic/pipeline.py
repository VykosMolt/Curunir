"""Pipeline facade: acquisition edge → understanding → world model → alerts.

`process_new_evidence` is the one call that carries every unprocessed
manifestation through normalization, extraction and world-model integration.
`process_fabric_changes` upgrades the watch path: each byte-level fabric
change becomes an interpreted semantic change with affected objects, claim
lifecycle updates, review items — and an evidence-bound mission alert whose
body explains the semantic difference instead of a hash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from argus.source_intelligence.models import digest_id
from curunir_operational.access import (Marking, inherited_marking,
                                         marking_from_record, most_restrictive)
from curunir_operational.workflow import WorkflowEngine

from .changes import explain_change, interpret_change
from .extract import extract_observations
from .normalize import NormalizationError, load_text, normalize_manifestation, normalized_document_id
from .store import SemanticStore
from .worldmodel import IntegrationContext, integrate_document, propose_cross_scheme_associations


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SemanticPipeline:
    store: SemanticStore
    custody_root: Path | str
    actor: str = "semantic-pipeline"
    marking: Marking = field(default_factory=lambda: Marking(
        owning_authority="curunir-semantic", releasability=("PUBLIC",)))
    now_fn: Callable[[], str] = _utc_now

    def context(self, marking=None) -> IntegrationContext:
        return IntegrationContext(store=self.store, actor=self.actor,
                                  marking=marking or self.marking, now_fn=self.now_fn)

    # ---- understanding ---------------------------------------------------

    def _failure_item_id(self, manifestation_id: str) -> str:
        return digest_id("review-processing", manifestation_id)

    def _record_processing_failure(self, manifestation_id: str, stage: str,
                                   error: Exception, marking=None) -> None:
        """A pipeline failure is durable state, never a silently dropped
        exception: the manifestation stays queued for retry until resolved.
        The failure item is ABOUT the manifestation, so it inherits the
        manifestation's marking, never a lower pipeline default."""
        from .contracts import ReviewItem
        item_id = self._failure_item_id(manifestation_id)
        detail = f"{stage} failed: {type(error).__name__}: {str(error)[:300]}"
        latest = self.store.latest_by_id("review_item", "item_id").get(item_id)
        if latest is not None and latest["status"] == "OPEN" and latest["detail"] == detail:
            return  # an identical failure is already queued; retries do not stack items
        item = ReviewItem(
            item_id=item_id, kind="PROCESSING_FAILED",
            subject_kind="fabric_manifestation", subject_id=manifestation_id,
            detail=detail,
            evidence_refs=(manifestation_id,), status="OPEN", resolution_note="",
            version=self.store.next_family_version("review_item", "item_id", item_id),
            recorded_time=self.now_fn(), marking=marking or self.marking)
        self.store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                          actor=self.actor)

    def _resolve_processing_failure(self, manifestation_id: str) -> None:
        from .contracts import ReviewItem
        item_id = self._failure_item_id(manifestation_id)
        latest = self.store.latest_by_id("review_item", "item_id").get(item_id)
        if latest is None or latest["status"] != "OPEN":
            return
        resolved = ReviewItem(
            item_id=item_id, kind="PROCESSING_FAILED",
            subject_kind="fabric_manifestation", subject_id=manifestation_id,
            detail=latest["detail"], evidence_refs=tuple(latest["evidence_refs"]),
            status="RESOLVED", resolution_note="reprocessed successfully",
            version=self.store.next_family_version("review_item", "item_id", item_id),
            recorded_time=self.now_fn(),
            # a re-append never re-classifies: keep the item's own marking
            marking=marking_from_record(latest["marking"])
            if isinstance(latest.get("marking"), dict) else latest["marking"])
        self.store.append("REVIEW_ITEM_RECORDED", resolved,
                          recorded_time=resolved.recorded_time, actor=self.actor)

    def _item_marking(self, manifestation: dict):
        """The marking derived state of a manifestation inherits: the join of
        the pipeline's marking and the manifestation's own."""
        own = manifestation.get("marking")
        if not isinstance(own, dict):
            return self.marking
        return most_restrictive([self.marking, marking_from_record(own)])

    def process_manifestation(self, manifestation: dict) -> dict[str, Any]:
        """Normalize, extract and integrate one manifestation (idempotent).

        Any failure is recorded as an OPEN PROCESSING_FAILED review item so a
        partially written manifestation is retried, not skipped as done."""
        manifestation_id = manifestation["manifestation_id"]
        now = self.now_fn()
        # the manifestation's understanding (document, observations, claims,
        # world objects) inherits the manifestation's OWN marking joined with
        # the pipeline's — a SPECIAL manifestation processed during a lower
        # pipeline run (a retry, a batch, a differently-marked route) is never
        # materialized into lower-marked derived state.
        item_marking = self._item_marking(manifestation)
        try:
            document = normalize_manifestation(
                self.store, manifestation, self.custody_root,
                now=now, actor=self.actor, marking=item_marking)
        except NormalizationError as error:
            self._record_processing_failure(manifestation_id, "normalization", error, item_marking)
            return {"manifestation_id": manifestation_id,
                    "status": "NORMALIZATION_FAILED", "error": str(error)}
        except Exception as error:
            # custody/IO failures are the same failure class: durable, retried
            self._record_processing_failure(manifestation_id, "normalization", error, item_marking)
            return {"manifestation_id": manifestation_id,
                    "status": "PROCESSING_FAILED", "error": f"{type(error).__name__}: {error}"}
        try:
            observations = extract_observations(self.store, document, now=self.now_fn(),
                                                actor=self.actor, marking=item_marking)
            ctx = self.context(item_marking)
            integration = integrate_document(ctx, document)
        except Exception as error:
            self._record_processing_failure(manifestation_id, "understanding", error, item_marking)
            return {"manifestation_id": manifestation_id,
                    "status": "PROCESSING_FAILED", "error": f"{type(error).__name__}: {error}"}
        self._resolve_processing_failure(manifestation_id)
        return {"manifestation_id": manifestation_id,
                "status": "PROCESSED", "document_id": document["document_id"],
                "new_observations": len(observations), "integration": integration}

    def process_new_evidence(self) -> dict[str, Any]:
        """Carry every unprocessed manifestation through the pipeline.

        A manifestation counts as done only when its document exists AND no
        processing failure is open for it; failures are retried."""
        processed = []
        known = {r["document_id"] for r in self.store.records_of("semantic_document")}
        open_failures = {r["subject_id"]
                         for r in self.store.latest_by_id("review_item", "item_id").values()
                         if r["kind"] == "PROCESSING_FAILED" and r["status"] == "OPEN"}
        for manifestation in self.store.records_of("fabric_manifestation"):
            manifestation_id = manifestation["manifestation_id"]
            if normalized_document_id(manifestation_id) in known \
                    and manifestation_id not in open_failures:
                continue
            processed.append(self.process_manifestation(manifestation))
        associations = propose_cross_scheme_associations(self.context())
        return {"processed": processed,
                "failed": [p for p in processed if p["status"] != "PROCESSED"],
                "association_proposals": len(associations)}

    # ---- semantic monitoring --------------------------------------------

    def _interpreted_pairs(self) -> set[tuple[str, str]]:
        return {(r["prior_manifestation_id"], r["current_manifestation_id"])
                for r in self.store.records_of("semantic_change")}

    def _complete_pair(self, prior_id: str, current_id: str,
                       raise_alerts: bool = True) -> list[str]:
        """An already-interpreted pair may still be missing its propagation
        tail (claim lifecycle, review item, alert) if the original run was
        interrupted after the change records landed. Completing is cheap —
        no re-normalization or re-classification — and idempotent."""
        from .changes import _propagate
        pair_changes = [c for c in self.store.records_of("semantic_change")
                        if c["prior_manifestation_id"] == prior_id
                        and c["current_manifestation_id"] == current_id]
        ctx = self.context()
        for change in pair_changes:
            _propagate(ctx, change)
        if not raise_alerts:
            return []
        # complete only MISSING alerts: an alert is keyed by its change_id, a
        # one-time fact — re-raising an existing one would append a no-op
        # "retriggered" transition on every poll, growing the append-only
        # chain without bound
        missing = [c for c in pair_changes
                   if c["change_class"] != "SEMANTICALLY_UNCHANGED"
                   and self.store.find_alert_by_dedup(
                       digest_id("semalert", c["change_id"])) is None]
        return self._raise_semantic_alerts(missing) if missing else []

    def process_fabric_changes(self, *, raise_alerts: bool = True) -> list[dict[str, Any]]:
        """Interpret watch-detected byte changes semantically, with alerts."""
        interpreted = self._interpreted_pairs()
        manifestations = {r["manifestation_id"]: r
                          for r in self.store.records_of("fabric_manifestation")}
        outcomes = []
        for fabric_change in self.store.records_of("fabric_change"):
            if fabric_change["change_type"] in ("RETRIEVAL_FAILURE",):
                continue
            prior_id = fabric_change.get("prior_ref", "")
            current_id = fabric_change.get("current_ref", "")
            if current_id not in manifestations:
                continue
            if (prior_id, current_id) in interpreted:
                self._complete_pair(prior_id, current_id, raise_alerts=raise_alerts)
                continue
            for manifestation_id in (prior_id, current_id):
                if manifestation_id in manifestations:
                    self.process_manifestation(manifestations[manifestation_id])
            current_document = next(
                (d for d in self.store.records_of("semantic_document")
                 if d["manifestation_id"] == current_id), None)
            current_text = load_text(self.store, current_document) \
                if current_document and current_document["normalized_sha256"] else ""
            ctx = self.context()
            changes = interpret_change(
                ctx, prior_id if prior_id in manifestations else "",
                current_id, watch_id=fabric_change.get("watch_id", ""),
                fabric_change_id=fabric_change["change_id"], current_text=current_text)
            interpreted.add((prior_id, current_id))
            alerts = []
            if raise_alerts:
                alerts = self._raise_semantic_alerts(changes)
            outcomes.append({"fabric_change_id": fabric_change["change_id"],
                             "semantic_changes": [c["change_class"] for c in changes],
                             "alerts": alerts})
        return outcomes

    def interpret_historical_discoveries(self) -> list[dict[str, Any]]:
        """A newly preserved historical manifestation is itself a semantic
        event: record the discovered historical state without pretending it
        happened today."""
        interpreted = {pair[1] for pair in self._interpreted_pairs() if not pair[0]}
        outcomes = []
        for manifestation in self.store.records_of("fabric_manifestation"):
            if manifestation["temporal_status"] != "HISTORICAL":
                continue
            if manifestation["manifestation_id"] in interpreted:
                self._complete_pair("", manifestation["manifestation_id"],
                                    raise_alerts=False)
                continue
            self.process_manifestation(manifestation)
            ctx = self.context()
            changes = interpret_change(ctx, "", manifestation["manifestation_id"])
            outcomes.append({"manifestation_id": manifestation["manifestation_id"],
                             "semantic_changes": [c["change_class"] for c in changes]})
        return outcomes

    def _raise_semantic_alerts(self, changes: list[dict[str, Any]]) -> list[str]:
        engine = WorkflowEngine(self.store)
        claims = self.store.current_claims()
        raised = []
        for change in changes:
            if change["change_class"] in ("SEMANTICALLY_UNCHANGED",):
                continue
            evidence = tuple(x for x in (change["prior_observation_id"],
                                         change["current_observation_id"],
                                         change["current_manifestation_id"]) if x)
            # the alert body (explain_change) quotes the change detail and the
            # affected propositions' statements; the alert inherits the change's
            # marking and every affected claim's marking so a compartmented
            # change never raises an alert a lower context can read
            alert_marking = inherited_marking(
                self.marking,
                [change.get("marking")]
                + [claims[cid].get("marking")
                   for cid in change["affected_claim_ids"] if cid in claims])
            alert_id, created = engine.raise_alert({
                "rule_id": "semantic-change", "rule_version": "0.1",
                "trigger": explain_change(self.store, change)[:900],
                "affected_ids": change["affected_object_ids"] or (change["subject_ref"],),
                "evidence_refs": evidence,
                "severity": "WARNING" if change["change_class"] not in
                ("SOURCE_RETRACTION", "SOURCE_CORRECTION") else "HIGH",
                "severity_rationale": f"semantic {change['change_class']} on watched source",
                "dedup_key": digest_id("semalert", change["change_id"]),
            }, marking=alert_marking, recorded_time=self.now_fn(), actor=self.actor)
            if created:
                raised.append(alert_id)
        return raised

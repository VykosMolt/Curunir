"""Semantic store: the fabric store plus semantic event types.

One store root now carries the full loop under a single hash chain: mission
workflow, source registry, plans, executions, manifestations, coverage,
watches, normalized documents, observations, claims and their lifecycle,
semantic changes, hypotheses, discriminators, collection routes and the
review queue — with the inherited export/import/replay guarantees.
"""
from __future__ import annotations

from curunir_fabric.store import FabricStore

SEMANTIC_EVENT_TYPES = {
    "SEMANTIC_DOCUMENT_RECORDED": "semantic_document",
    "SEMANTIC_OBSERVATION_RECORDED": "semantic_observation",
    "SEMANTIC_CLAIM_RECORDED": "semantic_claim",
    "SEMANTIC_CLAIM_STATE_RECORDED": "semantic_claim_state",
    "SEMANTIC_CHANGE_RECORDED": "semantic_change",
    "HYPOTHESIS_RECORDED": "hypothesis",
    "DISCRIMINATOR_RECORDED": "discriminator",
    "COLLECTION_ROUTE_RECORDED": "collection_route",
    "REVIEW_ITEM_RECORDED": "review_item",
}


class SemanticStore(FabricStore):
    EVENT_TYPES = {**FabricStore.EVENT_TYPES, **SEMANTIC_EVENT_TYPES}
    # the four re-appended families replay latest-wins; strict next-version
    # enforcement (inside the append lock, after catch-up) makes a stale
    # writer raise instead of silently shadowing another writer's update —
    # and guarantees log order equals version order, keeping latest_by_id
    # correct
    VERSIONED_RECORD_TYPES = {
        **FabricStore.VERSIONED_RECORD_TYPES,
        "hypothesis": "hypothesis_id",
        "discriminator": "discriminator_id",
        "collection_route": "route_id",
        "review_item": "item_id",
        # the proposition ledger itself: version plumbing already exists
        # (next_claim_version); enforcement makes a stale writer's claim
        # version raise instead of shadowing current propositions
        "semantic_claim": "claim_id",
        # escalation folds re-append requirements
        "information_requirement": "requirement_id",
    }

    def next_family_version(self, record_type: str, id_field: str,
                            record_id: str) -> int:
        known = self.latest_by_id(record_type, id_field).get(record_id)
        return (known.get("version", 1) + 1) if known else 1

    # ---- replayed views over semantic records ---------------------------

    def current_claims(self) -> dict[str, dict]:
        """Latest version per claim_id; every prior version stays in the log."""
        current: dict[str, dict] = {}
        for record in self.records_of("semantic_claim"):
            known = current.get(record["claim_id"])
            if known is None or record["version"] >= known["version"]:
                current[record["claim_id"]] = record
        return current

    def claim_states(self) -> dict[str, dict]:
        """Latest lifecycle state per claim; claims without one are CURRENT."""
        return self.latest_by_id("semantic_claim_state", "claim_id")

    def claim_state(self, claim_id: str) -> str:
        record = self.claim_states().get(claim_id)
        return record["state"] if record else "CURRENT"

    def current_hypotheses(self) -> dict[str, dict]:
        return self.latest_by_id("hypothesis", "hypothesis_id")

    def open_review_items(self) -> list[dict]:
        return [r for r in self.latest_by_id("review_item", "item_id").values()
                if r["status"] == "OPEN"]

    def documents_for_manifestation(self, manifestation_id: str) -> list[dict]:
        return [r for r in self.records_of("semantic_document")
                if r["manifestation_id"] == manifestation_id]

    def observations_for_document(self, document_id: str) -> list[dict]:
        return [r for r in self.records_of("semantic_observation")
                if r["document_id"] == document_id]

    def observations_for_manifestation(self, manifestation_id: str) -> list[dict]:
        return [r for r in self.records_of("semantic_observation")
                if r["manifestation_id"] == manifestation_id]

    def claims_referencing_observation(self, observation_id: str) -> list[dict]:
        return [r for r in self.current_claims().values()
                if observation_id in r["observation_ids"]]

    def next_claim_version(self, claim_id: str) -> int:
        known = self.current_claims().get(claim_id)
        return (known["version"] + 1) if known else 1

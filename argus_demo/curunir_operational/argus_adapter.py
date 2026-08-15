"""Adapter carrying ARGUS Source Intelligence evidence into the operational plane.

Consumes evidence bundles shaped in the Source Intelligence vocabulary
(SourceObject / DocumentEnvelope / assertion / KernelAdmissionProposal /
SourceRelationship field names) and preserves their stronger semantics:
identifiers, content hashes, identity/authority/independence/claim-basis and
review states, mapping precision, and source dependence. Nothing is stripped
to a bare URL; absent assessments stay UNKNOWN rather than being fabricated.
The adapter reads evidence records; it never writes into Source Intelligence
stores and never opens human-review gates.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from .canonical import digest_id
from .connectors import MissionDataConnector
from .contracts import EvidenceRef

ARGUS_EVIDENCE_MEDIA_TYPE = "application/vnd.curunir.argus-evidence+json"

# Source-relationship types (Source Intelligence vocabulary) that make two
# publications DEPENDENT on the same underlying basis.
DEPENDENT_RELATION_TYPES = frozenset({
    "SAME_PUBLICATION", "SAME_CONTENT", "RENDERING_VARIANT", "TRANSLATION", "MIRROR",
    "SYNDICATION", "QUOTED_FROM", "SUMMARIZES", "DERIVED_FROM", "COMMON_PRIMARY_SOURCE",
    "OFFICIAL_COPY", "ARCHIVED_COPY",
})


class ArgusEvidenceConnector(MissionDataConnector):
    connector_version = "1.0"
    media_type = ARGUS_EVIDENCE_MEDIA_TYPE

    def parse(self, body: bytes) -> Any:
        bundle = json.loads(body.decode("utf-8"))
        validate_evidence_bundle(bundle)
        return bundle


def validate_evidence_bundle(bundle: Mapping[str, Any]) -> None:
    if not isinstance(bundle, Mapping):
        raise ValueError("evidence bundle must be an object")
    source = bundle.get("source_object")
    if not isinstance(source, Mapping) or not source.get("source_object_id") or not source.get("content_sha256"):
        raise ValueError("evidence bundle requires source_object with source_object_id and content_sha256")
    document = bundle.get("document")
    if not isinstance(document, Mapping) or not document.get("document_id"):
        raise ValueError("evidence bundle requires document with document_id")
    assertion = bundle.get("assertion")
    if not isinstance(assertion, Mapping) or not assertion.get("assertion_id"):
        raise ValueError("evidence bundle requires assertion with assertion_id")


def dependence_group_id(bundle: Mapping[str, Any]) -> str | None:
    """Deterministic group id over the dependent-source set (sorted union), so
    every member of the group computes the same id regardless of import order."""
    own = bundle["source_object"]["source_object_id"]
    members = {own}
    for relation in bundle.get("source_relationships", []):
        if relation.get("relationship_type") in DEPENDENT_RELATION_TYPES:
            other = relation.get("source_object_a") if relation.get("source_object_a") != own else relation.get("source_object_b")
            other = other or relation.get("other_source_object_id")
            if other:
                members.add(other)
    if len(members) < 2:
        return None
    return digest_id("evgroup", *sorted(members))


def evidence_ref_from_bundle(bundle: Mapping[str, Any]) -> EvidenceRef:
    source = bundle["source_object"]
    document = bundle["document"]
    assertion = bundle["assertion"]
    admission = bundle.get("admission") or {}
    identity = bundle.get("identity") or {}
    unresolved = tuple(admission.get("dependencies", ()))
    return EvidenceRef(
        source_object_id=source["source_object_id"],
        document_id=document["document_id"],
        content_sha256=source["content_sha256"],
        assertion_id=assertion["assertion_id"],
        evidence_basis_id=assertion.get("evidence_basis_id") or "UNKNOWN_BASIS",
        identity_status=identity.get("status", "IDENTITY_UNKNOWN"),
        authority_state=document.get("authority_state", "AUTHORITY_NOT_ASSESSED"),
        independence_status=admission.get("independence_status", "UNRESOLVED"),
        claim_basis_status=admission.get("claim_basis_status", "UNKNOWN_BASIS"),
        review_state=assertion.get("review_state", "UNREVIEWED"),
        mapping_status=assertion.get("mapping_status", "UNKNOWN"),
        dependence_group_id=dependence_group_id(bundle),
        unresolved=unresolved,
    )

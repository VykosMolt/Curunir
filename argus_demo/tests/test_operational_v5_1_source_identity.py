from __future__ import annotations

import json

import pytest

from curunir_operational.v4.analysis import entity_record
from curunir_operational.v5_1.models import EvidenceRef, sha256
from curunir_operational.v5_1.source_identity import (
    ContentEdge, IdentityAssessment, assess_identity, content_edge,
    document_manifestation, group_manifestations, intellectual_work,
    is_mirror_pair, publication_record, resolve_source_roles, role_edge,
    translation_publication,
)

pytestmark = pytest.mark.no_db

AWARE = "2026-07-24T00:00:00+00:00"


def _ref(kind, detail="explicit statement captured in source metadata"):
    return EvidenceRef(kind, "source-object-alpha", None, detail)


def _work_and_publication():
    work = intellectual_work(title="Rapport sur les glaciers alpins", original_language="fr")
    publication = publication_record(work_id=work.work_id, venue_entity_id="venue-lac-blanc",
                                     language="fr", publication_time=AWARE)
    return work, publication


# --- role edges -------------------------------------------------------------

def test_role_edge_without_evidence_raises():
    with pytest.raises(ValueError, match="evidence"):
        role_edge(role="AUTHORED_BY", subject_id="pub-1", agent_entity_id="M. Aubert", evidence=())


def test_role_edge_rejects_unknown_role():
    with pytest.raises(ValueError, match="role"):
        role_edge(role="WROTE", subject_id="pub-1", agent_entity_id="M. Aubert",
                  evidence=(_ref("BYLINE"),))


def test_role_edge_rejects_foreign_evidence_kind():
    with pytest.raises(ValueError, match="vocabulary"):
        role_edge(role="AUTHORED_BY", subject_id="pub-1", agent_entity_id="M. Aubert",
                  evidence=(_ref("EXPLICIT_CITATION"),))


def test_published_by_refused_on_hosting_evidence_alone():
    with pytest.raises(ValueError, match="HOSTED_BY"):
        role_edge(role="PUBLISHED_BY", subject_id="pub-1",
                  agent_entity_id="stillwasser-hosting.example",
                  evidence=(_ref("DOMAIN_OWNERSHIP_RECORD"),))


def test_published_by_allowed_with_masthead_beside_hosting():
    edge = role_edge(role="PUBLISHED_BY", subject_id="pub-1", agent_entity_id="Belverne Presse",
                     evidence=(_ref("DOMAIN_OWNERSHIP_RECORD"), _ref("MASTHEAD_OR_IMPRINT")))
    assert edge.role == "PUBLISHED_BY" and len(edge.evidence) == 2


# --- content edges and the directional-evidence contract --------------------

def test_directional_translation_without_notice_downgraded():
    edge = content_edge(relation="TRANSLATED_FROM", source_id="pub-de", target_id="pub-fr",
                        evidence=(_ref("TRANSLATION_ALIGNMENT"),))
    assert edge.relation == "COMMON_EVIDENCE_BASIS"
    assert edge.downgrade is not None
    assert edge.downgrade.requested_relation == "TRANSLATED_FROM"
    assert "TRANSLATION_NOTICE" in edge.downgrade.missing_evidence_kinds


def test_directional_translation_with_notice_kept():
    edge = content_edge(relation="TRANSLATED_FROM", source_id="pub-de", target_id="pub-fr",
                        evidence=(_ref("TRANSLATION_NOTICE", "Übersetzt aus dem Französischen"),))
    assert edge.relation == "TRANSLATED_FROM" and edge.downgrade is None


def test_directional_downgrade_to_unknown_dependence():
    edge = content_edge(relation="DERIVED_FROM", source_id="pub-b", target_id="pub-a",
                        evidence=(_ref("PUBLICATION_TIMING", "pub-a precedes pub-b by two days"),))
    assert edge.relation == "UNKNOWN_DEPENDENCE"
    assert edge.downgrade is not None and edge.downgrade.granted_relation == "UNKNOWN_DEPENDENCE"


def test_constructor_refuses_unlicensed_directional_edge():
    with pytest.raises(ValueError, match="direction"):
        ContentEdge("edge-x", "TRANSLATED_FROM", "pub-a", "pub-b",
                    (_ref("TRANSLATION_ALIGNMENT"),), "UNSPECIFIED", (None, None),
                    {"relation_uncalibrated": 0.5}, None, AWARE)


def test_supersession_requires_explicit_notice():
    edge = content_edge(relation="SUPERSEDES", source_id="pub-b", target_id="pub-a",
                        evidence=(_ref("PUBLICATION_TIMING"),), scope="ANNEX_TABLES")
    assert edge.relation != "SUPERSEDES" and edge.downgrade is not None


def test_supersession_with_notice_requires_explicit_scope():
    notice = _ref("EXPLICIT_TEXT_SPAN", "this edition replaces the annex tables of the prior edition")
    with pytest.raises(ValueError, match="scope"):
        content_edge(relation="SUPERSEDES", source_id="pub-b", target_id="pub-a",
                     evidence=(notice,))
    edge = content_edge(relation="SUPERSEDES", source_id="pub-b", target_id="pub-a",
                        evidence=(notice,), scope="ANNEX_TABLES")
    assert edge.relation == "SUPERSEDES" and edge.scope == "ANNEX_TABLES"


# --- manifestation model ----------------------------------------------------

def test_mirror_manifestations_group_as_one_publication():
    _, publication = _work_and_publication()
    first = document_manifestation(publication_id=publication.publication_id,
                                   content_hash=sha256("capture body one"), media_type="text/html")
    second = document_manifestation(publication_id=publication.publication_id,
                                    content_hash=sha256("capture body two"), media_type="application/pdf")
    other = document_manifestation(publication_id="publication-elsewhere",
                                   content_hash=sha256("unrelated body"), media_type="text/html")
    groups = group_manifestations([first, second, other])
    assert groups[publication.publication_id] == (first, second) or \
        set(groups[publication.publication_id]) == {first, second}
    assert is_mirror_pair(first, second)
    assert not is_mirror_pair(first, other)


def test_translation_is_new_publication_with_dependence():
    work, publication = _work_and_publication()
    translated, edge = translation_publication(
        work, publication, venue_entity_id="venue-nordwind", language="de",
        evidence=(_ref("TRANSLATION_NOTICE", "Übersetzt aus dem Französischen von N. Keller"),))
    assert translated.work_id == work.work_id
    assert translated.publication_id != publication.publication_id
    assert edge.relation == "TRANSLATED_FROM" and edge.downgrade is None
    assert edge.source_id == translated.publication_id
    assert edge.target_id == publication.publication_id


def test_translation_without_notice_still_dependent_but_downgraded():
    work, publication = _work_and_publication()
    translated, edge = translation_publication(
        work, publication, venue_entity_id="venue-nordwind", language="de",
        evidence=(_ref("TRANSLATION_ALIGNMENT", "paragraph-aligned bilingual text"),))
    assert translated.publication_id != publication.publication_id
    assert edge.relation == "COMMON_EVIDENCE_BASIS" and edge.downgrade is not None


def test_manifestation_requires_content_hash():
    with pytest.raises(ValueError, match="SHA-256"):
        document_manifestation(publication_id="pub-1", content_hash="nothex",
                               media_type="text/html")


# --- metadata-driven role resolution ----------------------------------------

def test_host_only_metadata_yields_hosted_by_only():
    resolution = resolve_source_roles({"host_domain": "stillwasser-hosting.example"},
                                      subject_id="manifestation-1",
                                      source_object_id="source-object-alpha")
    roles = {edge.role for edge in resolution.role_edges}
    assert roles == {"HOSTED_BY"}
    assert "PUBLISHED_BY" not in roles


def test_publisher_metadata_yields_published_by_with_span_evidence():
    resolution = resolve_source_roles(
        {"publisher": "Éditions Belverne", "host_domain": "belverne-hosting.example"},
        {"publisher": "Publié par les Éditions Belverne"},
        subject_id="manifestation-1", source_object_id="source-object-alpha")
    by_role = {edge.role: edge for edge in resolution.role_edges}
    assert set(by_role) == {"PUBLISHED_BY", "HOSTED_BY"}
    assert by_role["PUBLISHED_BY"].evidence[0].detail == "Publié par les Éditions Belverne"
    assert by_role["PUBLISHED_BY"].evidence[0].evidence_kind == "EXPLICIT_METADATA"


def test_translation_notice_metadata_yields_translated_by():
    resolution = resolve_source_roles({"translation_notice": "Übersetzt von N. Keller"},
                                      subject_id="manifestation-2",
                                      source_object_id="source-object-alpha")
    assert {edge.role for edge in resolution.role_edges} == {"TRANSLATED_BY"}


def test_opaque_metadata_is_capability_failure():
    resolution = resolve_source_roles({"publisher": "??!!"}, subject_id="manifestation-3",
                                      source_object_id="source-object-alpha")
    assert not resolution.role_edges
    assert resolution.uninterpreted_fields == ("publisher",)
    outcome = resolution.outcomes[0]
    assert outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert outcome.capability_failure_class == "ROLE_METADATA_UNINTERPRETED"


def test_non_string_metadata_is_capability_failure():
    resolution = resolve_source_roles({"issuer": 404}, subject_id="manifestation-3",
                                      source_object_id="source-object-alpha")
    assert resolution.outcomes[0].outcome == "SYSTEM_CAPABILITY_FAILURE"


def test_unknown_metadata_field_is_capability_failure():
    resolution = resolve_source_roles({"colophon_sigil": "Nordwind"}, subject_id="manifestation-3",
                                      source_object_id="source-object-alpha")
    assert resolution.outcomes[0].outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert resolution.outcomes[0].capability_failure_class == "ROLE_METADATA_UNINTERPRETED"


def test_absent_metadata_is_epistemically_unresolvable_with_demonstration():
    resolution = resolve_source_roles({}, subject_id="manifestation-4",
                                      source_object_id="source-object-alpha")
    assert not resolution.role_edges and len(resolution.outcomes) == 1
    outcome = resolution.outcomes[0]
    assert outcome.outcome == "EPISTEMICALLY_UNRESOLVABLE"
    assert "publisher" in outcome.evidence_insufficiency_demonstration
    assert "host_domain" in outcome.evidence_insufficiency_demonstration
    assert set(resolution.absent_fields) >= {"publisher", "author", "host_domain"}


# --- identity resolution ----------------------------------------------------

def test_name_similarity_yields_ambiguous_identity():
    left = entity_record("PROGRAMME", "Aurora Shield")
    right = entity_record("VENDOR", "Aurora Shield Group")
    assessment = assess_identity(left, right, evidence=(
        _ref("LEGAL_NAME", "both records carry near-identical registered names"),))
    assert assessment.outcome == "AMBIGUOUS_IDENTITY"


def test_supported_merge_constructor_rejects_name_only_evidence():
    with pytest.raises(ValueError, match="name similarity"):
        IdentityAssessment("assessment-x", "entity-a", "ORGANIZATION", "entity-b",
                           "ORGANIZATION", "SAME_ENTITY_SUPPORTED",
                           (_ref("LEGAL_NAME"), _ref("LANGUAGE_ALIAS")),
                           "names look alike", None, AWARE)


def test_programme_vendor_pair_needs_licensing_evidence():
    left = entity_record("PROGRAMME", "Aurora Shield")
    right = entity_record("VENDOR", "Aurora Shield Group")
    assessment = assess_identity(left, right, evidence=(
        _ref("ORGANIZATIONAL_HIERARCHY"), _ref("DOMAIN")))
    assert assessment.outcome == "AMBIGUOUS_IDENTITY"
    with pytest.raises(ValueError, match="class-conflict"):
        IdentityAssessment("assessment-y", left.entity_id, "PROGRAMME", right.entity_id,
                           "VENDOR", "SAME_ENTITY_SUPPORTED",
                           (_ref("ORGANIZATIONAL_HIERARCHY"), _ref("DOMAIN")),
                           "structural overlap", None, AWARE)


def test_official_identifier_and_explicit_statement_merge():
    left = entity_record("PROGRAMME", "Aurora Shield")
    right = entity_record("VENDOR", "Aurora Shield Group")
    assessment = assess_identity(left, right, evidence=(
        _ref("OFFICIAL_IDENTIFIER", "both records cite register entry HR-77413"),
        _ref("EXPLICIT_INSTITUTIONAL_ATTRIBUTION",
             "the register names the vendor as sole operator of the programme")))
    assert assessment.outcome == "SAME_ENTITY_SUPPORTED"


def test_shared_registered_identifier_merges():
    left = entity_record("ORGANIZATION", "Institut Belverne",
                         external_identifiers={"registry": "R-100"})
    right = entity_record("ORGANIZATION", "Belverne Institute",
                          external_identifiers={"registry": "R-100"})
    assessment = assess_identity(left, right)
    assert assessment.outcome == "SAME_ENTITY_SUPPORTED"
    assert any(ref.evidence_kind == "OFFICIAL_IDENTIFIER" for ref in assessment.evidence)


def test_identifier_conflict_supports_difference():
    left = entity_record("ORGANIZATION", "Institut Belverne",
                         external_identifiers={"registry": "R-100"})
    right = entity_record("ORGANIZATION", "Belverne Institut",
                          external_identifiers={"registry": "R-200"})
    assessment = assess_identity(left, right)
    assert assessment.outcome == "DIFFERENT_ENTITY_SUPPORTED"
    assert assessment.evidence


def test_identity_unresolvable_requires_demonstration():
    left = entity_record("ORGANIZATION", "Nordwind Kollektiv")
    right = entity_record("ORGANIZATION", "Nordwind Collective")
    demonstration = ("no official register, publication metadata, or explicit attribution "
                     "for either name exists in the available public record")
    assessment = assess_identity(left, right, unresolvable_demonstration=demonstration)
    assert assessment.outcome == "EPISTEMICALLY_UNRESOLVABLE"
    with pytest.raises(ValueError, match="demonstration"):
        IdentityAssessment("assessment-z", left.entity_id, "ORGANIZATION", right.entity_id,
                           "ORGANIZATION", "EPISTEMICALLY_UNRESOLVABLE", (),
                           "cannot tell", "too short", AWARE)


def test_no_evidence_no_demonstration_is_ambiguous_never_merged():
    left = entity_record("PRODUCT", "Falcon Ledger")
    right = entity_record("ORGANIZATION", "Falcon Ledger")
    assessment = assess_identity(left, right)
    assert assessment.outcome == "AMBIGUOUS_IDENTITY"


# --- serialization ----------------------------------------------------------

def test_records_serialize_to_json():
    edge = content_edge(relation="TRANSLATED_FROM", source_id="pub-de", target_id="pub-fr",
                        evidence=(_ref("TRANSLATION_ALIGNMENT"),))
    payload = json.dumps(edge.to_record())
    assert "downgrade" in payload and "TRANSLATED_FROM" in payload
    resolution = resolve_source_roles({"author": "M. Aubert"}, subject_id="manifestation-5",
                                      source_object_id="source-object-alpha")
    assert json.loads(json.dumps(resolution.to_record()))["subject_id"] == "manifestation-5"

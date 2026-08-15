"""D34 — document-level authority, and the fabrication it must refuse.

The whole risk of this repair is that it invents an issuer.  Naming the familiar
institution, the hosting site, or the nearest capitalised organisation would
raise recoverable recall and be worse than the gap it closes, because a wrong
authority is published with the same confidence as a right one.

So the positives here are paired with anti-fabrication negatives, and the
negatives are the point.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import authority as AU
from curunir_operational.v5_8_1 import regions as RG
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db

RECITAL = "<p>Recalling resolution WHA65.4 on noncommunicable disease,</p>"

WITH_AUTHORITY = f"""<html><body>
<h2>SEVENTY-SEVENTH WORLD HEALTH ASSEMBLY WHA77.1</h2>
<p>Agenda item 11</p>
{RECITAL}
</body></html>"""

ONLY_HOST = f"""<html><body>
<h2>Fact sheet</h2>
<p>World Health Organization. All rights reserved. https://www.who.int/</p>
{RECITAL}
</body></html>"""

NO_AUTHORITY = f"""<html><body>
<h2>Background note</h2>
<p>The following observations were collected during the review period.</p>
{RECITAL}
</body></html>"""

TWO_AUTHORITIES = f"""<html><body>
<h2>SEVENTY-SEVENTH WORLD HEALTH ASSEMBLY WHA77.1</h2>
<p>EXECUTIVE BOARD adopted the text at its 154th session.</p>
{RECITAL}
</body></html>"""


def _pipeline(markup: str, document_id: str, url: str = ""):
    regions = RG.segment(markup, source_id=document_id, source_family_id="t")
    authority = AU.build_document_authority(
        regions=regions, document_id=document_id, final_url=url)
    recital = next(r for r in regions if ST.is_recital(r.text))
    context = ST.build_structural_context(
        regions=regions, region_id=recital.region_id, document_id=document_id,
        manifestation_id=f"{document_id}-m", authority_context=authority)
    binding = V2.bind(candidate_id="u", text=recital.text, language="en",
                      structural_context=context)
    return authority, context, binding, recital


# --- the paired cases (section 18) -----------------------------------------

def test_explicit_masthead_authority_with_an_instrument_relation_recovers():
    authority, context, binding, _ = _pipeline(WITH_AUTHORITY, "d1")
    assert authority.instrument_id == "WHA77.1"
    assert authority.document_authority_state == "AUTHORITY_ESTABLISHED"
    assert binding.role_binding_state == "ROLE_BINDING_RECOVERABLE"


def test_only_a_hosting_organisation_does_not_establish_authority():
    authority, context, binding, _ = _pipeline(ONLY_HOST, "d2",
                                               url="https://www.who.int/x")
    assert authority.document_authority_state == "AUTHORITY_NOT_ESTABLISHED"
    assert binding.role_binding_state == "ROLE_BINDING_PARTIAL"


def test_no_authority_evidence_is_partial():
    authority, _, binding, _ = _pipeline(NO_AUTHORITY, "d3")
    assert authority.document_authority_state == "AUTHORITY_NOT_ESTABLISHED"
    assert binding.role_binding_state == "ROLE_BINDING_PARTIAL"


def test_two_plausible_authorities_are_ambiguous():
    authority, _, _, _ = _pipeline(TWO_AUTHORITIES, "d4")
    assert authority.document_authority_state == "AUTHORITY_AMBIGUOUS"


def test_the_recital_text_is_byte_identical_across_all_four_cases():
    """The distinction must come from structure, never from the span."""
    texts = {_pipeline(m, f"d{n}")[3].text
             for n, m in enumerate((WITH_AUTHORITY, ONLY_HOST, NO_AUTHORITY,
                                    TWO_AUTHORITIES))}
    assert len(texts) == 1


# --- anti-fabrication (section 19) -----------------------------------------

def test_host_is_never_promoted_to_issuer():
    authority, _, _, _ = _pipeline(ONLY_HOST, "d5", url="https://www.who.int/x")
    assert not any(c.establishes_authority
                   for c in authority.hosting_authority_candidates)
    state, best, reason = authority.resolution()
    assert state == "AUTHORITY_NOT_ESTABLISHED"
    assert "publisher or host" in reason


def test_an_organ_without_an_instrument_relation_does_not_establish():
    """Naming an organisation is never sufficient for *document authority*.

    D32 may still recover the recital from that organ statement as a nearby
    in-section governing region -- that is a different relation, correctly
    found by a different mechanism.  What must not happen is D34 declaring a
    document-level authority on the strength of a name alone.
    """
    markup = f"<html><body><h2>Notes</h2><p>World Health Assembly</p>{RECITAL}</body></html>"
    authority, context, _, _ = _pipeline(markup, "d6")
    assert authority.instrument_id == ""
    assert authority.document_authority_state == "AUTHORITY_NOT_ESTABLISHED"
    assert context.applicable_issuing_authority == ""
    assert not any(c.establishes_authority
                   for c in authority.issuing_authority_candidates
                   + authority.adopting_authority_candidates)


def test_an_organ_far_from_the_recital_does_not_establish_authority():
    """No in-section organ either: the honest answer is PARTIAL."""
    filler = "".join(f"<p>Paragraph {n} records a detail.</p>" for n in range(15))
    markup = (f"<html><body><h2>Notes</h2><p>World Health Assembly</p>{filler}"
              f"{RECITAL}</body></html>")
    authority, context, binding, _ = _pipeline(markup, "d6b")
    assert authority.document_authority_state == "AUTHORITY_NOT_ESTABLISHED"
    assert context.governing_resolution()[0] == "GOVERNING_CLAUSE_ABSENT"
    assert binding.role_binding_state == "ROLE_BINDING_PARTIAL"


def test_authority_candidates_carry_provenance():
    authority, _, _, _ = _pipeline(WITH_AUTHORITY, "d7")
    for candidate in authority.issuing_authority_candidates:
        assert candidate.region_id
        assert candidate.source_text_hash
        assert candidate.source_offsets is not None
        assert candidate.instrument_relation


def test_distant_authority_is_not_copied_into_the_local_span():
    _, _, binding, recital = _pipeline(WITH_AUTHORITY, "d8")
    assert "ASSEMBLY" not in binding.predicate_head.upper()
    assert "ASSEMBLY" not in binding.predicate_complement.upper()
    assert binding.subject_span is None


def test_publishing_and_hosting_relations_are_excluded_by_construction():
    for relation in AU.NON_AUTHORITY_RELATIONS:
        candidate = AU.AuthorityCandidate(
            candidate_id="c", authority_text="Some Organisation",
            evidence_class="EXPLICIT_MASTHEAD_AUTHORITY", region_id="r",
            source_offsets=(0, 5), source_text_hash="h",
            instrument_relation="WHA77.1", applicability=relation,
            confidence=0.9)
        assert not candidate.establishes_authority


def test_a_non_establishing_evidence_class_cannot_establish():
    candidate = AU.AuthorityCandidate(
        candidate_id="c", authority_text="Some Publisher",
        evidence_class="EXPLICIT_PUBLISHER", region_id="r",
        source_offsets=(0, 5), source_text_hash="h",
        instrument_relation="WHA77.1",
        applicability="AUTHORITY_GOVERNS_DOCUMENT", confidence=0.99)
    assert not candidate.establishes_authority


def test_unknown_states_are_refused_at_construction():
    with pytest.raises(ValueError):
        AU.AuthorityCandidate(
            candidate_id="c", authority_text="x",
            evidence_class="NOT_A_CLASS", region_id="r", source_offsets=None,
            source_text_hash="h", instrument_relation="i",
            applicability="AUTHORITY_GOVERNS_DOCUMENT", confidence=0.5)
    with pytest.raises(ValueError):
        AU.DocumentAuthorityContext(
            context_id="c", document_id="d", manifestation_id="m",
            intellectual_work_id="w", instrument_id="i", instrument_title="t",
            instrument_type="X", instrument_scope="MANIFESTATION",
            document_authority_state="AUTHORITY_LOOKS_FINE")


def test_authority_does_not_override_a_nearer_governing_region():
    """An in-section organ statement outranks a document-level authority."""
    markup = f"""<html><body>
<h2>SEVENTY-SEVENTH WORLD HEALTH ASSEMBLY WHA77.1</h2>
<p>The Executive Board,</p>
{RECITAL}
</body></html>"""
    _, context, _, _ = _pipeline(markup, "d9")
    state, chosen, _ = context.governing_resolution()
    assert state in ("GOVERNING_CLAUSE_UNIQUE", "GOVERNING_CLAUSE_AMBIGUOUS")


def test_empty_authority_establishes_nothing():
    empty = AU.empty_authority("d")
    assert empty.resolution()[0] == "AUTHORITY_NOT_ESTABLISHED"

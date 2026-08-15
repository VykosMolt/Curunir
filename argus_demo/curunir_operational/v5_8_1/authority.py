"""V5.8.1 §12-§16 — D34: document-level authority and instrument context.

D32 transports a structural relation that already exists.  D34 constructs one
that does not: the relation between a proposition and the body that issued or
adopted the instrument it sits in.  Measured coverage after D32 was 4 of 120
units, because a recital's organ is usually not a neighbouring region — in the
WHO governing-body PDFs it is a masthead on the first page, related to the
recital only through the instrument they share.

The whole risk here is fabrication.  It would be easy, and wrong, to name the
familiar institution, the hosting site, or the nearest capitalised organisation.
So authority is established only from evidence that carries an explicit relation
to the instrument, every candidate keeps its region, offsets and hash, and the
roles that are *not* authority — publisher, host, repository, secretariat,
signatory — are kept in separate fields so they can never be silently promoted.

An authority this module cannot evidence is AUTHORITY_NOT_ESTABLISHED.  That is
an answer, not a failure.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id

# ===========================================================================
# §13 — evidence classes
# ===========================================================================

EVIDENCE_CLASSES: tuple[str, ...] = (
    "EXPLICIT_TITLE_PAGE_ISSUER", "EXPLICIT_MASTHEAD_AUTHORITY",
    "FORMAL_ADOPTION_OR_ENACTMENT_CLAUSE", "EXPLICIT_SIGNATURE_AUTHORITY",
    "TRUSTED_MANIFESTATION_METADATA", "EXPLICIT_INSTRUMENT_SERIES_METADATA",
    "DOCUMENT_INTERNAL_DEFINITION", "EXPLICIT_PUBLISHER",
    "EXPLICIT_HOST_OR_REPOSITORY", "AUTHORITY_RELATION_UNRESOLVED",
)

#: §13 — only these can establish issuance or adoption.  Publishing, hosting and
#: repository relations are deliberately excluded: a site that serves a treaty
#: did not adopt it.
AUTHORITY_ESTABLISHING = frozenset({
    "EXPLICIT_TITLE_PAGE_ISSUER", "EXPLICIT_MASTHEAD_AUTHORITY",
    "FORMAL_ADOPTION_OR_ENACTMENT_CLAUSE", "EXPLICIT_SIGNATURE_AUTHORITY",
    "EXPLICIT_INSTRUMENT_SERIES_METADATA",
})

AUTHORITY_STATES: tuple[str, ...] = (
    "AUTHORITY_ESTABLISHED", "AUTHORITY_RECOVERABLE", "AUTHORITY_AMBIGUOUS",
    "AUTHORITY_NOT_ESTABLISHED", "AUTHORITY_CONTRADICTED",
)

APPLICABILITY_RELATIONS: tuple[str, ...] = (
    "AUTHORITY_GOVERNS_DOCUMENT", "AUTHORITY_GOVERNS_INSTRUMENT",
    "AUTHORITY_GOVERNS_SECTION", "AUTHORITY_GOVERNS_RECITAL_GROUP",
    "SIGNATORY_ATTESTS_DOCUMENT", "PUBLISHER_PUBLISHES_MANIFESTATION",
    "HOST_HOSTS_MANIFESTATION",
)

#: §15 — these carry no issuance or adoption.
NON_AUTHORITY_RELATIONS = frozenset({
    "PUBLISHER_PUBLISHES_MANIFESTATION", "HOST_HOSTS_MANIFESTATION"})

#: Two candidates within this confidence band are both materially plausible.
AUTHORITY_SEPARATION_MARGIN = 0.15
#: How many leading regions may carry title-page or masthead evidence.
FRONT_MATTER_REGIONS = 12


# --- surface evidence, all requiring an instrument relation to count -------

#: An instrument identifier: a document symbol, a treaty series number, a
#: national act citation.  This is what ties an organ named in front matter to
#: the body of the text; without it a masthead is decoration.
_INSTRUMENT_SYMBOL = re.compile(
    r"\b(WHA\d{2}\.\d{1,2}|EB\d{3}\.R\d{1,2}|A\d{2}/\d{1,3}|"
    r"CETS\s*\d{3}|ETS\s*\d{3}|"
    r"A/RES/\d{1,3}/\d{1,3}|"
    r"\d{4}/\d{1,4}/(?:EU|EC)|"
    r"decreto\s+legislativo\s+\d{1,3}|legge\s+\d{1,3})\b", re.IGNORECASE)

#: An organ that can issue or adopt.  This is *not* used on its own: it must
#: co-occur with an instrument relation in front matter, an enactment clause or
#: a signature block.  Naming an organisation is never sufficient.
_ORGAN = re.compile(
    r"(World Health Assembly|Health Assembly|Executive Board|"
    r"General Assembly|Security Council|Committee of Ministers|"
    r"Conference of the Parties|Council of Europe|"
    r"Assembl[eé]e mondiale de la Sant[eé]|Conseil de l'Europe|"
    r"Comit[eé] des Ministres|"
    r"Asamblea Mundial de la Salud|Consejo Ejecutivo|"
    r"Assemblea mondiale della sanit[aà]|"
    r"IL PRESIDENTE DELLA REPUBBLICA|EL PRESIDENTE DE LA REP[UÚ]BLICA|"
    r"ВСЕМИРНАЯ АССАМБЛЕЯ ЗДРАВООХРАНЕНИЯ|Всемирная ассамблея здравоохранения|"
    r"ИСПОЛНИТЕЛЬНЫЙ КОМИТЕТ|Генеральная Ассамблея|"
    r"جمعية الصحة العالمية|المجلس التنفيذي|الجمعية العامة)",
    re.IGNORECASE)

_ADOPTION_VERB = re.compile(
    r"\b(adopts?|adopted|decides?|resolves?|enacts?|promulgates?|"
    r"adopte|d[eé]cide|adopta|decreta|adotta|"
    r"постановляет|принимает|утверждает|"
    r"تعتمد|تقرر)\b", re.IGNORECASE)

_SIGNATURE = re.compile(
    r"\b(signed|done at|fait [aà]|hecho en|fatto a|подписано|"
    r"in witness whereof|en foi de quoi)\b", re.IGNORECASE)

#: Host and publisher surface forms.  Recognised so they can be *excluded*.
_HOST_OR_PUBLISHER = re.compile(
    r"(all rights reserved|©|\bwww\.|https?://|"
    r"published by|publi[eé] par|editorial|imprenta|"
    r"World Health Organization\b(?!\s+(?:Assembly|Executive)))",
    re.IGNORECASE)


@dataclass(frozen=True)
class AuthorityCandidate(Record):
    """One proposal for one authority role, with its provenance."""

    candidate_id: str
    authority_text: str
    evidence_class: str
    region_id: str
    source_offsets: tuple[int, int] | None
    source_text_hash: str
    instrument_relation: str
    applicability: str
    confidence: float

    def __post_init__(self) -> None:
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise ValueError(f"unknown evidence class {self.evidence_class!r}")
        if self.applicability not in APPLICABILITY_RELATIONS:
            raise ValueError(f"unknown applicability {self.applicability!r}")

    @property
    def establishes_authority(self) -> bool:
        return (self.evidence_class in AUTHORITY_ESTABLISHING
                and self.applicability not in NON_AUTHORITY_RELATIONS
                and bool(self.instrument_relation))


@dataclass(frozen=True)
class DocumentAuthorityContext(Record):
    """§12 — who issued the instrument this proposition sits in, and on what evidence."""

    context_id: str
    document_id: str
    manifestation_id: str
    intellectual_work_id: str

    instrument_id: str
    instrument_title: str
    instrument_type: str
    instrument_scope: str

    issuing_authority_candidates: tuple[AuthorityCandidate, ...] = ()
    adopting_authority_candidates: tuple[AuthorityCandidate, ...] = ()
    publishing_authority_candidates: tuple[AuthorityCandidate, ...] = ()
    hosting_authority_candidates: tuple[AuthorityCandidate, ...] = ()
    signatory_candidates: tuple[AuthorityCandidate, ...] = ()
    secretariat_candidates: tuple[AuthorityCandidate, ...] = ()

    title_page_region_ids: tuple[str, ...] = ()
    masthead_region_ids: tuple[str, ...] = ()
    enactment_clause_ids: tuple[str, ...] = ()
    signature_region_ids: tuple[str, ...] = ()
    metadata_record_ids: tuple[str, ...] = ()

    applicable_section_ids: tuple[str, ...] = ()
    applicable_recital_group_ids: tuple[str, ...] = ()
    applicable_proposition_ids: tuple[str, ...] = ()

    document_authority_state: str = "AUTHORITY_NOT_ESTABLISHED"
    provenance_hash: str = ""
    recorded_time: str = ""

    def __post_init__(self) -> None:
        if self.document_authority_state not in AUTHORITY_STATES:
            raise ValueError(
                f"unknown authority state {self.document_authority_state!r}")

    def resolution(self) -> tuple[str, AuthorityCandidate | None, str]:
        """§14 — the authority, or an explicit statement that there is none."""
        establishing = [c for c in
                        (self.issuing_authority_candidates
                         + self.adopting_authority_candidates)
                        if c.establishes_authority]
        if not establishing:
            if (self.publishing_authority_candidates
                    or self.hosting_authority_candidates):
                return ("AUTHORITY_NOT_ESTABLISHED", None,
                        "only a publisher or host is known; neither issues nor "
                        "adopts an instrument")
            return ("AUTHORITY_NOT_ESTABLISHED", None,
                    "no evidence carries an explicit relation to the instrument")
        ranked = sorted(establishing, key=lambda c: -c.confidence)
        best = ranked[0]
        rivals = [c for c in ranked[1:]
                  if best.confidence - c.confidence < AUTHORITY_SEPARATION_MARGIN
                  and _flat(c.authority_text) != _flat(best.authority_text)]
        if rivals:
            return ("AUTHORITY_AMBIGUOUS", None,
                    f"{len(rivals) + 1} authorities are materially plausible; "
                    "the document does not decide between them")
        if best.applicability in ("AUTHORITY_GOVERNS_DOCUMENT",
                                  "AUTHORITY_GOVERNS_INSTRUMENT"):
            return ("AUTHORITY_ESTABLISHED", best,
                    f"{best.evidence_class} with an explicit instrument relation")
        return ("AUTHORITY_RECOVERABLE", best,
                f"{best.evidence_class} applying to {best.applicability}")


def _flat(text: str) -> str:
    return " ".join((text or "").split()).casefold()


# ===========================================================================
# Construction
# ===========================================================================

def build_document_authority(*, regions: Sequence[Any], document_id: str,
                             manifestation_id: str = "",
                             intellectual_work_id: str = "",
                             final_url: str = "") -> DocumentAuthorityContext:
    """Construct the authority context from front matter, enactment and signature.

    The instrument identifier is what makes an organ an authority rather than a
    decoration: it is the explicit relation §13 requires.  A manifestation with
    an organ but no instrument identifier yields AUTHORITY_NOT_ESTABLISHED, and
    so does one with only a host or publisher.
    """
    instrument_id = ""
    instrument_title = ""
    for region in regions[:FRONT_MATTER_REGIONS]:
        body = region.text or ""
        # A symbol standing in running text is usually a *citation* to another
        # instrument -- "Recalling resolution WHA65.4" -- and reading it as this
        # document's identity would attach the wrong authority to every
        # proposition in the file.  The identity is taken only from a heading or
        # from a region that also names the organ, which is where a document
        # states what it is.
        eligible = (region.heading_relation == "IS_HEADING"
                    or _ORGAN.search(body) is not None)
        if eligible and not instrument_id:
            match = _INSTRUMENT_SYMBOL.search(body)
            if match:
                instrument_id = match.group(0)
        if not instrument_title and region.heading_relation == "IS_HEADING":
            instrument_title = body[:200]

    issuing: list[AuthorityCandidate] = []
    adopting: list[AuthorityCandidate] = []
    publishing: list[AuthorityCandidate] = []
    hosting: list[AuthorityCandidate] = []
    signatories: list[AuthorityCandidate] = []
    masthead_ids: list[str] = []
    enactment_ids: list[str] = []
    signature_ids: list[str] = []

    def candidate(text: str, evidence: str, region, offsets, relation: str,
                  applicability: str, confidence: float) -> AuthorityCandidate:
        return AuthorityCandidate(
            stable_id("v5-8-1-authcand", region.region_id, text[:60], evidence),
            text, evidence, region.region_id, offsets,
            sha256([text]), relation, applicability, round(confidence, 4))

    for position, region in enumerate(regions):
        body = region.text or ""
        organ = _ORGAN.search(body)
        front_matter = position < FRONT_MATTER_REGIONS

        if organ and front_matter and instrument_id:
            masthead_ids.append(region.region_id)
            issuing.append(candidate(
                organ.group(0), "EXPLICIT_MASTHEAD_AUTHORITY", region,
                (organ.start(), organ.end()), instrument_id,
                "AUTHORITY_GOVERNS_INSTRUMENT", 0.88))
        elif organ and _ADOPTION_VERB.search(body):
            enactment_ids.append(region.region_id)
            adopting.append(candidate(
                organ.group(0), "FORMAL_ADOPTION_OR_ENACTMENT_CLAUSE", region,
                (organ.start(), organ.end()), instrument_id or region.region_id,
                "AUTHORITY_GOVERNS_DOCUMENT", 0.9))
        elif organ and _SIGNATURE.search(body):
            signature_ids.append(region.region_id)
            signatories.append(candidate(
                organ.group(0), "EXPLICIT_SIGNATURE_AUTHORITY", region,
                (organ.start(), organ.end()), instrument_id or "",
                "SIGNATORY_ATTESTS_DOCUMENT", 0.75))
        elif organ and _HOST_OR_PUBLISHER.search(body):
            # An organisation appearing beside a rights notice or a URL is
            # publishing or hosting, and §15 says that is not issuance.
            hosting.append(candidate(
                organ.group(0), "EXPLICIT_HOST_OR_REPOSITORY", region,
                (organ.start(), organ.end()), "",
                "HOST_HOSTS_MANIFESTATION", 0.6))

    if final_url:
        host = re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", final_url)
        if host:
            class _Synthetic:
                region_id = f"{document_id}-manifestation"
            hosting.append(AuthorityCandidate(
                stable_id("v5-8-1-authcand", document_id, host, "HOST"),
                host, "EXPLICIT_HOST_OR_REPOSITORY",
                _Synthetic.region_id, None, sha256([host]), "",
                "HOST_HOSTS_MANIFESTATION", 0.5))

    context = DocumentAuthorityContext(
        context_id=stable_id("v5-8-1-authctx", document_id),
        document_id=document_id, manifestation_id=manifestation_id,
        intellectual_work_id=intellectual_work_id,
        instrument_id=instrument_id, instrument_title=instrument_title,
        instrument_type=("RESOLUTION" if instrument_id.upper().startswith(
            ("WHA", "EB", "A/RES")) else "INSTRUMENT" if instrument_id else "UNKNOWN"),
        instrument_scope="MANIFESTATION",
        issuing_authority_candidates=tuple(issuing),
        adopting_authority_candidates=tuple(adopting),
        publishing_authority_candidates=tuple(publishing),
        hosting_authority_candidates=tuple(hosting),
        signatory_candidates=tuple(signatories),
        masthead_region_ids=tuple(masthead_ids),
        enactment_clause_ids=tuple(enactment_ids),
        signature_region_ids=tuple(signature_ids),
        provenance_hash=sha256([document_id, instrument_id,
                                *(c.source_text_hash for c in issuing + adopting)]),
        recorded_time=now_utc())
    state, _, _ = context.resolution()
    return DocumentAuthorityContext(
        **{**context.__dict__, "document_authority_state": state})


def empty_authority(document_id: str = "") -> DocumentAuthorityContext:
    return DocumentAuthorityContext(
        context_id=stable_id("v5-8-1-authctx", document_id or "-"),
        document_id=document_id, manifestation_id="", intellectual_work_id="",
        instrument_id="", instrument_title="", instrument_type="UNKNOWN",
        instrument_scope="MANIFESTATION",
        provenance_hash=sha256([document_id]), recorded_time=now_utc())


__all__ = [
    "EVIDENCE_CLASSES", "AUTHORITY_ESTABLISHING", "AUTHORITY_STATES",
    "APPLICABILITY_RELATIONS", "NON_AUTHORITY_RELATIONS",
    "AUTHORITY_SEPARATION_MARGIN", "AuthorityCandidate",
    "DocumentAuthorityContext", "build_document_authority", "empty_authority",
]

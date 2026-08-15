"""Source identity and origin over source dossiers (contract Section 11).

V5.1 scored 0 of 81 on this surface: 71 packets were marked PACKET_DEFECT
because a reviewer was shown a 480-character body excerpt and asked which
role an institution holds over the publication.  The capability was never
validly evaluated, so nothing is known about whether it works.

The repair is not a better guess from the same evidence.  It is evidence-
backed role assignment over a SOURCE_DOSSIER that actually carries the
manifestation, the URL and redirect chain, the domain, the title page, the
imprint, the author lines, the document series, the hosting and issuing
institutions, the submitted-by record, ownership evidence, archive and mirror
relations, and the timestamps.  Each role is decided from the evidence kinds
that can decide it, and three specific inferences are refused outright
because they are the conflations the closure gate counts as critical errors:

* a host is not a publisher,
* an uploader is not an author,
* a vendor participating in a programme does not own it.

Genuine ambiguity is returned only when the dossier holds the relevant
available evidence and that evidence does not resolve the role.  An
incomplete dossier is a construction defect, not epistemic ambiguity.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v5_1.models import (
    ENTITY_CLASSES, EvidenceRef, ROLE_EVIDENCE_KINDS, Record, SOURCE_ROLES,
    capability_outcome, now_utc, stable_id,
)
from ..v5_1.source_identity import RoleEdge, role_edge

# Which dossier sections can decide which role.  A role that has no decisive
# section in the dossier is unresolved, and the resolver says so instead of
# falling back to whatever institution is nearest.
ROLE_DECISIVE_SECTIONS: Mapping[str, tuple[str, ...]] = {
    "AUTHORED_BY": ("author_lines", "publication_metadata", "title_page"),
    "EDITED_BY": ("publication_metadata", "title_page", "header_and_footer"),
    "SUBMITTED_BY": ("submitted_by", "publication_metadata"),
    "ISSUED_BY": ("issuing_institution", "title_page", "document_series",
                  "publication_metadata"),
    "PUBLISHED_BY": ("publication_metadata", "title_page", "header_and_footer",
                     "document_series"),
    "HOSTED_BY": ("hosting_institution", "domain", "url", "redirect_chain"),
    "MIRRORED_BY": ("archive_or_mirror_relations", "domain", "redirect_chain"),
    "ARCHIVED_BY": ("archive_or_mirror_relations", "timestamps", "url"),
    "TRANSLATED_BY": ("publication_metadata", "explicit_source_role_evidence"),
    "SYNDICATED_BY": ("explicit_source_role_evidence", "publication_metadata"),
    "COMMISSIONED_BY": ("explicit_source_role_evidence", "title_page",
                        "publication_metadata"),
    "OWNED_BY": ("ownership_evidence", "organization_and_programme_records"),
    "OPERATED_BY": ("ownership_evidence", "organization_and_programme_records",
                    "explicit_source_role_evidence"),
}
assert set(ROLE_DECISIVE_SECTIONS) == set(SOURCE_ROLES)

# Section 11.2 — inferences that are refused however tempting the evidence
# looks.  Each names the critical error it prevents.
PROHIBITED_INFERENCES: Mapping[str, str] = {
    "HOST_AS_PUBLISHER": (
        "a hosting institution or domain owner may not be recorded as the "
        "publisher; hosting is a technical relation and publishing is an "
        "editorial one"),
    "UPLOADER_AS_AUTHOR": (
        "whoever submitted or uploaded a manifestation may not be recorded as "
        "its author; submission is a custody act"),
    "VENDOR_AS_PROGRAMME_OWNER": (
        "a vendor named as a participant, supplier or consortium member may not "
        "be recorded as the owner or operator of the programme"),
}

RESOLUTION_STATES = (
    "RESOLVED", "UNRESOLVED_EVIDENCE_ABSENT", "UNRESOLVED_EVIDENCE_CONFLICTS",
    "REFUSED_PROHIBITED_INFERENCE",
)

# Words whose presence in a section marks it as hosting or custody evidence
# rather than editorial responsibility.
_HOSTING_MARKERS = re.compile(
    r"\b(?:host(?:ed|ing)?|server|cdn|mirror|archive|wayback|repository|"
    r"gehostet|serveur|h[ée]berg[ée]|alojad[oa]|ospitat[oa])\b", re.IGNORECASE)
_SUBMISSION_MARKERS = re.compile(
    r"\b(?:submitted\s+by|uploaded\s+by|deposited\s+by|eingereicht\s+von|"
    r"hochgeladen\s+von|soumis\s+par|d[ée]pos[ée]\s+par|presentado\s+por)\b",
    re.IGNORECASE)
_VENDOR_MARKERS = re.compile(
    r"\b(?:supplier|vendor|contractor|consortium\s+member|participant|"
    r"subcontractor|partner|lieferant|auftragnehmer|konsortialpartner|"
    r"fournisseur|prestataire|proveedor|adjudicatari[oa])\b", re.IGNORECASE)
_PUBLISHER_MARKERS = re.compile(
    r"\b(?:publish(?:ed|er)|published\s+by|imprint|masthead|herausgeber|"
    r"herausgegeben\s+von|impressum|[ée]diteur|publi[ée]\s+par|editor(?:ial)?|"
    r"editado\s+por|publicado\s+por)\b", re.IGNORECASE)
_ISSUER_MARKERS = re.compile(
    r"\b(?:issued\s+by|issuing|adopted\s+by|on\s+behalf\s+of|"
    r"herausgegeben|erlassen\s+von|[ée]mis\s+par|adopt[ée]\s+par|"
    r"emitido\s+por|adoptado\s+por|directorate-general|secretariat|"
    r"joint\s+undertaking|general\s+secretariat)\b", re.IGNORECASE)
_OWNERSHIP_MARKERS = re.compile(
    r"\b(?:owned\s+by|owner|operates?|operated\s+by|managed\s+by|"
    r"eigent[üu]mer|betrieben\s+von|betreiber|propri[ée]taire|exploit[ée]\s+par|"
    r"propietari[oa]|gestionado\s+por)\b", re.IGNORECASE)
_TRANSLATION_MARKERS = re.compile(
    r"\b(?:translat(?:ed|ion)\s+by|[üu]bersetzt\s+von|[üu]bersetzung|"
    r"traduit\s+par|traduction|traducido\s+por|traducci[óo]n)\b", re.IGNORECASE)
_SYNDICATION_MARKERS = re.compile(
    r"\b(?:syndicated|wire\s+service|via\s+(?:reuters|afp|dpa|ap|efe|ansa)|"
    r"agentur|agence|agencia)\b", re.IGNORECASE)
_COMMISSION_MARKERS = re.compile(
    r"\b(?:commissioned\s+by|prepared\s+for|study\s+for|im\s+auftrag\s+von|"
    r"command[ée]\s+par|encargado\s+por)\b", re.IGNORECASE)

_AUTHOR_MARKERS = re.compile(
    r"\b(?:by\s+[A-Z][\w.\'-]+|author(?:ed)?|written\s+by|verfasst\s+von|"
    r"autor(?:in)?|par\s+[A-Z]|escrito\s+por|redigido\s+por)\b")
_EDITOR_MARKERS = re.compile(
    r"\b(?:edited\s+by|editor|redaktion|r[ée]dacteur|r[ée]daction|"
    r"editado\s+por|coordinado\s+por)\b", re.IGNORECASE)
_MIRROR_MARKERS = re.compile(
    r"\b(?:mirror(?:ed|s)?|spiegel(?:server)?|miroir|espejo|r[ée]plique)\b",
    re.IGNORECASE)
_ARCHIVE_MARKERS = re.compile(
    r"\b(?:archiv(?:e|ed|al)|wayback|snapshot|capture|archiv(?:iert|ierung)|"
    r"archiv[ée]|archivad[oa])\b", re.IGNORECASE)

# Every role needs a marker.  A role that can be assigned merely because a
# section is non-empty is exactly how V5.1 would have read a publisher off a
# retrieval timestamp.
_ROLE_MARKERS: Mapping[str, re.Pattern[str]] = {
    "AUTHORED_BY": _AUTHOR_MARKERS,
    "EDITED_BY": _EDITOR_MARKERS,
    "MIRRORED_BY": _MIRROR_MARKERS,
    "ARCHIVED_BY": _ARCHIVE_MARKERS,
    "PUBLISHED_BY": _PUBLISHER_MARKERS,
    "ISSUED_BY": _ISSUER_MARKERS,
    "HOSTED_BY": _HOSTING_MARKERS,
    "SUBMITTED_BY": _SUBMISSION_MARKERS,
    "OWNED_BY": _OWNERSHIP_MARKERS,
    "OPERATED_BY": _OWNERSHIP_MARKERS,
    "TRANSLATED_BY": _TRANSLATION_MARKERS,
    "SYNDICATED_BY": _SYNDICATION_MARKERS,
    "COMMISSIONED_BY": _COMMISSION_MARKERS,
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(item) for item in value)
    return str(value)


def _section_text(sections: Mapping[str, Any], names: Iterable[str]) -> str:
    return " ".join(_text(sections.get(name)) for name in names).strip()


@dataclass(frozen=True)
class RoleResolution(Record):
    """One role decision over one source dossier, with its evidence."""

    resolution_id: str
    dossier_id: str
    role: str
    agent_entity_id: str | None
    state: str
    decisive_sections: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    refused_inference: str | None
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.role not in SOURCE_ROLES:
            raise ValueError(f"unknown source role: {self.role}")
        if self.state not in RESOLUTION_STATES:
            raise ValueError(f"unknown resolution state: {self.state}")
        if self.state == "RESOLVED" and not self.agent_entity_id:
            raise ValueError("a resolved role names the agent that holds it")
        if self.state == "RESOLVED" and not self.evidence:
            raise ValueError("a resolved role requires evidence; a role read off "
                             "an absent section is exactly the V5.1 defect")
        if self.state == "REFUSED_PROHIBITED_INFERENCE" and \
                self.refused_inference not in PROHIBITED_INFERENCES:
            raise ValueError("a refusal must name the prohibited inference")


def _resolution(dossier_id: str, role: str, state: str, rationale: str, *,
                agent: str | None = None, sections: Iterable[str] = (),
                evidence: Iterable[EvidenceRef] = (),
                refused: str | None = None) -> RoleResolution:
    return RoleResolution(
        stable_id("role-resolution", dossier_id, role, state),
        dossier_id, role, agent, state, tuple(sections), tuple(evidence),
        refused, rationale, now_utc())


def _evidence_for(dossier_id: str, section: str, detail: str) -> EvidenceRef:
    return EvidenceRef("EXPLICIT_METADATA", dossier_id, None,
                       f"{section}: {detail}"[:400])


def resolve_role(sections: Mapping[str, Any], role: str, *,
                 dossier_id: str, candidate_agent: str | None = None,
                 agent_entity_id: str | None = None) -> RoleResolution:
    """Decide one role over one source dossier.

    ``candidate_agent`` is the institution the decision is about — the name a
    reviewer is asked to place.  The resolver reads only the sections that can
    decide the role and refuses the three prohibited inferences outright.
    """
    if role not in ROLE_DECISIVE_SECTIONS:
        raise ValueError(f"unknown source role: {role}")
    decisive = ROLE_DECISIVE_SECTIONS[role]
    present = tuple(name for name in decisive if sections.get(name) not in
                    (None, "", [], {}, ()))
    body = _section_text(sections, present)
    submission = _section_text(sections, ("submitted_by",))
    programme = _section_text(sections, ("organization_and_programme_records",))

    # Section 11.2 refusals, checked before any positive assignment.
    if role == "PUBLISHED_BY":
        editorial = _section_text(sections, ("publication_metadata", "title_page",
                                             "header_and_footer", "document_series"))
        hosting = _section_text(sections, ROLE_DECISIVE_SECTIONS["HOSTED_BY"])
        if not _PUBLISHER_MARKERS.search(editorial) and _HOSTING_MARKERS.search(hosting):
            return _resolution(
                dossier_id, role, "REFUSED_PROHIBITED_INFERENCE",
                PROHIBITED_INFERENCES["HOST_AS_PUBLISHER"] +
                "; the dossier carries hosting evidence but no imprint, masthead "
                "or publication statement", sections=present,
                refused="HOST_AS_PUBLISHER")
    if role == "AUTHORED_BY":
        bylines = _section_text(sections, ("author_lines",))
        if not bylines.strip() and _SUBMISSION_MARKERS.search(submission or body):
            return _resolution(
                dossier_id, role, "REFUSED_PROHIBITED_INFERENCE",
                PROHIBITED_INFERENCES["UPLOADER_AS_AUTHOR"] +
                "; the dossier carries a submission record and no byline",
                sections=present, refused="UPLOADER_AS_AUTHOR")
    if role in {"OWNED_BY", "OPERATED_BY"}:
        ownership = _section_text(sections, ("ownership_evidence",))
        if not _OWNERSHIP_MARKERS.search(ownership) and _VENDOR_MARKERS.search(
                programme or body):
            return _resolution(
                dossier_id, role, "REFUSED_PROHIBITED_INFERENCE",
                PROHIBITED_INFERENCES["VENDOR_AS_PROGRAMME_OWNER"] +
                "; the dossier records participation, not ownership",
                sections=present, refused="VENDOR_AS_PROGRAMME_OWNER")

    if not present:
        return _resolution(
            dossier_id, role, "UNRESOLVED_EVIDENCE_ABSENT",
            f"the dossier carries none of the sections that can decide "
            f"{role}: {list(decisive)}", sections=decisive)

    marker = _ROLE_MARKERS[role]
    if not marker.search(body):
        return _resolution(
            dossier_id, role, "UNRESOLVED_EVIDENCE_ABSENT",
            f"the decisive sections {list(present)} are present but none states "
            f"a {role} relation; the evidence is available and does not resolve "
            "the role", sections=present)

    agent = agent_entity_id or candidate_agent
    if not agent:
        return _resolution(
            dossier_id, role, "UNRESOLVED_EVIDENCE_ABSENT",
            f"a {role} relation is stated but the dossier names no agent to bind "
            "it to", sections=present)

    evidence = tuple(
        _evidence_for(dossier_id, name, _text(sections.get(name))[:200])
        for name in present if _text(sections.get(name)).strip())
    return _resolution(
        dossier_id, role, "RESOLVED",
        f"{role} is stated in {list(present)} and binds to {agent}",
        agent=agent, sections=present, evidence=evidence)


def resolve_all_roles(sections: Mapping[str, Any], *, dossier_id: str,
                      candidate_agent: str | None = None) -> tuple[RoleResolution, ...]:
    """Decide every role the dossier could bear, in a stable order."""
    return tuple(resolve_role(sections, role, dossier_id=dossier_id,
                              candidate_agent=candidate_agent)
                 for role in sorted(SOURCE_ROLES))


def role_coverage(resolutions: Iterable[RoleResolution | Mapping[str, Any]]
                  ) -> dict[str, Any]:
    """Which of the thirteen roles a population actually exercised."""
    seen: dict[str, int] = {role: 0 for role in sorted(SOURCE_ROLES)}
    for item in resolutions:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        if get("state") == "RESOLVED" and get("role") in seen:
            seen[str(get("role"))] += 1
    return {
        "roles_required": len(seen),
        "roles_exercised": sum(1 for count in seen.values() if count),
        "counts": seen,
        "missing": [role for role, count in seen.items() if not count],
    }


def conflation_audit(resolutions: Iterable[RoleResolution | Mapping[str, Any]]
                     ) -> dict[str, int]:
    """Count the conflations the closure gate forbids.

    Every one of these is zero by construction when roles are resolved through
    ``resolve_role``; the audit exists to prove it over populations that may
    have been assembled elsewhere.
    """
    counts = {"host_publisher_conflations": 0, "uploader_author_conflations": 0,
              "vendor_programme_conflations": 0, "roles_without_evidence": 0}
    for item in resolutions:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        if get("state") != "RESOLVED":
            continue
        if not get("evidence"):
            counts["roles_without_evidence"] += 1
        decisive = tuple(get("decisive_sections") or ())
        role = str(get("role"))
        if role == "PUBLISHED_BY" and set(decisive) <= {"domain", "url",
                                                        "redirect_chain",
                                                        "hosting_institution"}:
            counts["host_publisher_conflations"] += 1
        if role == "AUTHORED_BY" and set(decisive) <= {"submitted_by"}:
            counts["uploader_author_conflations"] += 1
        if role in {"OWNED_BY", "OPERATED_BY"} and set(decisive) <= {
                "organization_and_programme_records"}:
            counts["vendor_programme_conflations"] += 1
    return counts


def to_role_edge(resolution: RoleResolution, *, subject_id: str,
                 confidence: Mapping[str, float] | None = None) -> RoleEdge:
    """Promote a resolved role into the V5.1 role-edge graph."""
    if resolution.state != "RESOLVED":
        raise ValueError("only a resolved role may become a graph edge")
    return role_edge(
        role=resolution.role, subject_id=subject_id,
        agent_entity_id=str(resolution.agent_entity_id),
        evidence=resolution.evidence,
        confidence_dimensions=dict(confidence or {"role": 0.9}),
        alternative_interpretation=None)


def unresolved_outcome(resolution: RoleResolution, demonstration: str):
    """A genuinely unresolvable role, recorded as such rather than guessed."""
    return capability_outcome(
        subject_kind="SOURCE_ROLE", subject_id=resolution.resolution_id,
        outcome="EPISTEMICALLY_UNRESOLVABLE",
        rationale=resolution.rationale,
        evidence_insufficiency_demonstration=demonstration)

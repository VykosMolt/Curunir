"""V5.8.1 §7-§10 — D32: typed structural-context transport.

`regions.py` already knows that a recital sits under an enacting clause, that a
list item hangs off a lead-in, and that a table cell belongs to a column header.
Nothing carried that knowledge to the role binder.  `InvariantContext` had no
field for it, the production callers passed only local text, and `roles_v2.py`
was left to decide from a five-word window whether a governing clause exists.

It cannot.  Measured on the diagnostic reference: 35 of 120 units are
governing-clause recitals splitting 20 PARTIAL / 15 RECOVERABLE, and
left-context length is identical either side (median five words).  The
distinguishing variable is not in the window at all — it is whether a *specific*
enacting clause governs this span.

So the structure is not rediscovered here.  It is preserved and transported, as
typed relations between identified regions, with offsets and lineage intact.
Nothing in this module reads text to guess at document shape that regions.py has
already typed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id

# ===========================================================================
# §8 — typed structural relations
# ===========================================================================

RELATION_TYPES: tuple[str, ...] = (
    "RECITAL_GOVERNED_BY_ENACTMENT",
    "LIST_ITEM_INHERITS_LEAD_IN_SUBJECT",
    "LIST_ITEM_INHERITS_LEAD_IN_PREDICATE",
    "TABLE_CELL_INHERITS_COLUMN_HEADER",
    "TABLE_CELL_INHERITS_ROW_HEADER",
    "SUBCLAUSE_INHERITS_SECTION_SUBJECT",
    "COORDINATE_INHERITS_SUBJECT",
    "COORDINATE_INHERITS_PREDICATE",
    "HEADING_SUPPLIES_INSTITUTIONAL_CONTEXT",
    "DEFINITION_SUPPLIES_ENTITY_BINDING",
    "GRAMMATICAL_CONTINUATION_INHERITS_FRAME",
    "OPERATIVE_CLAUSE_ISSUED_BY_ORGAN",
)

READING_ORDER_STATES: tuple[str, ...] = (
    "READING_ORDER_ESTABLISHED", "READING_ORDER_RECOVERABLE",
    "LAYOUT_RECONSTRUCTION_REQUIRED", "PACKET_CONSTRUCTION_DEFECT",
    "READING_ORDER_NOT_APPLICABLE",
)
#: HTML carries its own structure, so reading order does not arise there.  A PDF
#: must state one: the default is NOT_APPLICABLE and a PDF-derived context that
#: leaves it at the default is a wiring defect, not a sound document.

#: How far a bounded structural search may look, in regions.  §10 forbids an
#: unrestricted nearest-clause scan across the document.
MAX_STRUCTURAL_LOOKBACK = 12
#: Two governing candidates whose confidence differs by less than this are both
#: materially plausible, and the binding is ambiguous rather than recovered.
GOVERNING_SEPARATION_MARGIN = 0.15


@dataclass(frozen=True)
class StructuralRelation(Record):
    """One typed relation between two identified regions."""

    relation_id: str
    relation_type: str
    source_region_id: str
    target_region_id: str
    source_offsets: tuple[int, int] | None
    structural_path: tuple[str, ...]
    confidence: float
    applicability: tuple[str, ...]
    evidence: str

    def __post_init__(self) -> None:
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(f"unknown structural relation {self.relation_type!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence out of range")


@dataclass(frozen=True)
class StructuralClauseContext(Record):
    """§7 — everything the role binder may know about where a span sits.

    Every identifier refers to a frozen region record.  There are no anonymous
    text blobs: an inherited role is named by the region that carries it, never
    by pasting its words into the local span.
    """

    context_id: str
    document_id: str
    manifestation_id: str
    current_region_id: str
    current_proposition_id: str

    parent_region_ids: tuple[str, ...] = ()
    heading_ancestor_ids: tuple[str, ...] = ()
    section_id: str = ""
    document_part_type: str = "UNKNOWN"

    preceding_proposition_ids: tuple[str, ...] = ()
    following_proposition_ids: tuple[str, ...] = ()

    governing_clause_candidates: tuple[str, ...] = ()
    governing_clause_relation_types: tuple[str, ...] = ()
    governing_clause_confidences: tuple[float, ...] = ()

    shared_subject_candidates: tuple[str, ...] = ()
    shared_predicate_candidates: tuple[str, ...] = ()

    recital_group_id: str = ""
    enacting_clause_ids: tuple[str, ...] = ()
    recital_to_enactment_relations: tuple[StructuralRelation, ...] = ()

    list_lead_in_ids: tuple[str, ...] = ()
    table_header_context_ids: tuple[str, ...] = ()
    table_row_context_ids: tuple[str, ...] = ()

    #: D34.  The document-level authority reaches the binder through this same
    #: transport rather than a second path, so one wiring audit covers both.
    document_authority_state: str = "AUTHORITY_NOT_ESTABLISHED"
    applicable_issuing_authority: str = ""
    applicable_authority_region_id: str = ""
    applicable_authority_relation: str = ""
    authority_scope: str = ""

    reading_order_state: str = "READING_ORDER_NOT_APPLICABLE"
    required_context_ids: tuple[str, ...] = ()
    context_source_hash: str = ""
    recorded_time: str = ""

    def __post_init__(self) -> None:
        if self.reading_order_state not in READING_ORDER_STATES:
            raise ValueError(f"unknown reading-order state "
                             f"{self.reading_order_state!r}")
        if len(self.governing_clause_candidates) != \
                len(self.governing_clause_confidences):
            raise ValueError("governing candidates and confidences disagree")

    # -- §10 bounded resolution -------------------------------------------
    def governing_resolution(self) -> tuple[str, str | None, str]:
        """Whether a governing clause is uniquely, absently or ambiguously present."""
        if not self.governing_clause_candidates:
            return ("GOVERNING_CLAUSE_ABSENT", None,
                    "no typed governing relation reaches this span")
        distinct = tuple(dict.fromkeys(self.governing_clause_candidates))
        if len(distinct) > 1:
            return ("GOVERNING_CLAUSE_AMBIGUOUS", None,
                    f"{len(distinct)} distinct typed governor parents")
        return ("GOVERNING_CLAUSE_UNIQUE", distinct[0],
                "one distinct governor parent through declared structural lineage")


# ===========================================================================
# Region-level evidence.  These read the *typing* regions.py produced, not the
# prose, except where a clause's own operative form is the structural fact.
# ===========================================================================

_RECITAL_OPENER = re.compile(
    r"^\s*(whereas|considering|recogniz|recognis|recall|reaffirm|noting|"
    r"bearing in mind|desiring|having regard|convinced|determined|emphasi[sz]|"
    r"welcoming|acknowledging|aware|affirming|"
    r"consid[eé]rant|reconnaissant|rappelant|r[eé]affirmant|notant|vu\b|"
    r"considerando|reconociendo|recordando|reafirmando|deseando|teniendo|"
    r"visto|vista|visti|viste|considerato|riconoscendo|"
    r"in der erw[aä]gung|gest[uü]tzt auf|eingedenk|[uü]berzeugt|"
    r"وإذ|إذ\s|اعترافا|إدراكا|رغبة في|اقتناعا|"
    r"учитывая|принимая во внимание|сознавая|признавая|стремясь|"
    r"подтверждая|ссылаясь|отмечая|будучи|рассмотрев|заслушав|напоминая)",
    re.IGNORECASE)

#: An enacting clause names an organ and issues an operative direction.  This is
#: the structural fact that makes a recital recoverable, and it is recognised
#: from the clause's own operative form, not from proximity.
_ENACTING_CLAUSE = re.compile(
    r"(ПОСТАНОВЛЯЕТ|ПОРУЧАЕТ|ПРОСИТ|ПРЕДЛАГАЕТ|ПРИЗЫВАЕТ|РЕКОМЕНДУЕТ|"
    r"УТВЕРЖДАЕТ|ПРИНИМАЕТ|НАЗНАЧАЕТ|УПОЛНОМОЧИВАЕТ|ОДОБРЯЕТ|"
    r"DECIDES|REQUESTS|URGES|RECOMMENDS|ADOPTS|APPOINTS|AUTHORIZES|ENDORSES|"
    r"DECIDE|PRIE|INVITE|RECOMMANDE|ADOPTE|"
    r"DECIDE|PIDE|INSTA|RECOMIENDA|ADOPTA|"
    r"تقرر|تطلب|تحث|توصي|تعتمد|تعيّن|"
    r"\b(?:The|La|Le|Il|El)\s+(?:Assembly|Board|Committee|Council|Conference|"
    r"Commission|Health Assembly|Executive Board)\b|"
    r"IL PRESIDENTE DELLA REPUBBLICA|EL PRESIDENTE DE|DECRETA|"
    r"Ассамблея здравоохранения|Исполнительный комитет)")

_OPERATIVE_NUMBER = re.compile(r"^\s*\d{1,2}\.\s*[A-ZА-Я؀-ۿ]")

#: The organ that issues the instrument.  A recital's subject is this, not any
#: one operative paragraph: "The Health Assembly, [recitals], DECIDES ...".  An
#: instrument with five operative paragraphs still has one subject, so treating
#: each operative as a rival governor would report ambiguity that the document
#: does not have.  The organ header may stand before the recitals or after them,
#: so the search is bidirectional within the section and bounded either way.
_ORGAN_HEADER = re.compile(
    r"^\s*(?:The|La|Le|Il|El|Die|Der)?\s*"
    r"(Health Assembly|World Health Assembly|Executive Board|General Assembly|"
    r"Security Council|Committee of Ministers|Conference of the Parties|"
    r"Assembl[eé]e|Conseil ex[eé]cutif|Asamblea|Consejo Ejecutivo|"
    r"IL PRESIDENTE DELLA REPUBBLICA|EL PRESIDENTE DE LA REP[UÚ]BLICA|"
    r"Ассамблея здравоохранения|Исполнительный комитет|Генеральная Ассамблея|"
    r"جمعية الصحة العالمية|المجلس التنفيذي)\s*,?\s*$",
    re.IGNORECASE)


def is_organ_header(text: str) -> bool:
    """Whether this region names the organ issuing the instrument."""
    body = " ".join((text or "").split())
    if _ORGAN_HEADER.match(body):
        return True
    # "The Health Assembly, having considered the report," -- the organ plus a
    # participial preamble is still the organ statement.
    return bool(re.match(
        r"^\s*(?:The|La|Le|El)\s+(?:Health Assembly|World Health Assembly|"
        r"Executive Board|General Assembly|Assembly|Board|Committee|Council|"
        r"Conference|Commission)\s*,", body, re.IGNORECASE))



def is_recital(text: str) -> bool:
    return bool(_RECITAL_OPENER.match(text or ""))


def is_enacting_clause(text: str) -> bool:
    body = text or ""
    if _ENACTING_CLAUSE.search(body):
        return True
    return bool(_OPERATIVE_NUMBER.match(body) and len(body.split()) >= 4)


_BROAD_ORGAN_STATEMENT = re.compile(
    r"(?:"
    r"(?:[CС]емьдесят|Шестьдесят|Пятьдесят|\d+)[^,\n]{0,80}"
    r"(?:сесси[яи]\s+)?Всемирной\s+ассамблеи\s+здравоохранения|"
    r"Всемирная\s+ассамблея\s+здравоохранения|"
    r"Исполнительный\s+комитет|"
    r"(?:La\s+)?\d+[.ªº]*\s+Asamblea\s+Mundial\s+de\s+la\s+Salud|"
    r"(?:El\s+)?Consejo\s+Ejecutivo|"
    r"(?:Le\s+)?Conseil\s+ex[ée]cutif|"
    r"(?:The\s+)?(?:World\s+)?Health\s+Assembly|"
    r"(?:The\s+)?Executive\s+Board|"
    r"IL\s+PRESIDENTE\s+DELLA\s+REPUBBLICA|"
    r"EL\s+PRESIDENTE\s+DE\s+LA\s+REP[UÚ]BLICA|"
    r"جمعية\s+الصحة\s+العالمية|المجلس\s+التنفيذي"
    r")",
    re.IGNORECASE,
)
_ENUMERATED_ITEM = re.compile(
    r"^\s*(?:\(\d{1,3}\)|\d{1,3}[.)])\s+",
    re.UNICODE,
)
_TERMINAL_PUNCTUATION = re.compile(r"[.!?。؟]\s*$")
_PERFORMATIVE_AT_START = re.compile(
    r"^\s*(?:\d{1,3}[.)]\s*)?(?:"
    r"ПОСТАНОВЛЯЕТ|ПОРУЧАЕТ|ПРОСИТ|ПРЕДЛАГАЕТ|ПРИЗЫВАЕТ|"
    r"РЕКОМЕНДУЕТ|УТВЕРЖДАЕТ|ПРИНИМАЕТ|НАЗНАЧАЕТ|"
    r"УПОЛНОМОЧИВАЕТ|ОДОБРЯЕТ|DECIDES|REQUESTS|URGES|"
    r"RECOMMENDS|ADOPTS|APPOINTS|AUTHORIZES|ENDORSES|AUTORISE|"
    r"DECIDE|PRIE|INVITE|RECOMMANDE|ADOPTE|PIDE|INSTA|"
    r"RECOMIENDA|DECRETA|تقرر|تطلب|تحث|توصي|تعتمد|تعيّن"
    r")\b",
    re.IGNORECASE,
)
_LIST_FRAME = re.compile(
    r"(?:"
    r"\b(?:should|must|shall|requests?|urges?|recommends?|authorizes?)\b|"
    r"\b(?:devrait|doit|recommand[ée]e|prie|invite|autorise|existe)\b|"
    r"\b(?:deber\w*|debe\w*|pide|insta|recomienda|autoriza)\b|"
    r"\b(?:soll|muss|bezeichnen)\b|"
    r"ПОРУЧАЕТ|ПРОСИТ|ПРИЗЫВАЕТ|РЕКОМЕНДУЕТ|УПОЛНОМОЧИВАЕТ|"
    r"فيما\s+يلي|من\s+الضروري|ينبغي|يجب|تطلب|تحث|توصي"
    r")",
    re.IGNORECASE,
)
_RECITAL_CONTINUATION = re.compile(
    r"^\s*(?:"
    r"[a-zà-öø-ÿа-яё]|"
    r"и\b|а\b|но\b|что\b|котор|and\b|or\b|et\b|ou\b|y\b|e\b|و"
    r")",
)
_LOCAL_FINITE = re.compile(
    r"(?:"
    r"\b(?:is|are|was|were|shall|should|must|has|have)\b|"
    r"\b(?:est|sont|doit|doivent)\b|"
    r"\b(?:es|son|debe|deben|deber[ií]a\w*)\b|"
    r"\b(?:ist|sind|muss|müssen|soll|sollen)\b|"
    r"\b(?:использу\w+|понима\w+|позволя\w+|долж\w+|явля\w+|"
    r"[А-Яа-яЁё]+(?:ется|ются))\b|"
    r"\b(?:يجب|ينبغي|يمكن|تشمل|يشمل)\b"
    r")",
    re.IGNORECASE,
)
_COORDINATE_OR_ITEM = re.compile(
    r"^\s*(?:"
    r"\(?[a-zа-я]\)\s+|"
    r"\d[\d\s.,]*\s+|"
    r"но\s+и\b|а\s+также\b|and\s+also\b|as\s+well\s+as\b"
    r")",
    re.IGNORECASE,
)
_BIBLIOGRAPHIC_OR_METADATA = re.compile(
    r"(?:"
    r"\b(?:ISBN|ISSN|doi|https?://)\b|"
    r"\b(?:издание|издательство|Женева|Geneva|Gen[eè]ve)\b|"
    r"^\s*(?:Первое|Второе|Третье|Fourth|Third|Second|First)\s+заседание\b"
    r")",
    re.IGNORECASE,
)
_PROPER_NAME_CONTINUATION = re.compile(
    r"(?:Организации\s*$)",
    re.IGNORECASE,
)
_PROPER_NAME_TARGET = re.compile(
    r"^\s*(?:Объединенных\s+Наций)\b",
    re.IGNORECASE,
)
MAX_GOVERNOR_EVIDENCE_CHARS = 80

# V581B M6.  A source line whose final token is a relative pronoun or
# subordinator has just OPENED a subordinate frame; what the next line
# continues is that subordinate clause, not a governing one.  Closed
# grammatical class per corpus language, grammar-derived.
_SUBORDINATE_TAIL = re.compile(
    r"(?:котор(?:ый|ая|ое|ые|ого|ой|ых|ым|ыми|ую)|чтобы"
    r"|which|whose|whereby|wherein"
    r"|lequel|laquelle|lesquels|lesquelles|dont|auquel"
    r"|cuyo|cuya|quale|quali"
    r"|الذي|التي|الذين"
    r"|welche[rsmn]?|wobei|wodurch)\s*$",
    re.IGNORECASE,
)

# V581B M6.  A leading digit group that is NOT an enumerator (those are
# refused at entry) is transparent to the continuation test: "2023-2030 pour
# lutter…" continues its predecessor exactly as "pour lutter…" would.
_LEADING_DIGIT_GROUP = re.compile(r"^[\s\d‐-―–—-]+")


def _sentence_initial(text: str) -> bool | None:
    """Does this line begin a sentence?  Leading digits, enumerators and
    punctuation are transparent; scripts without case return None."""
    for ch in (text or "").strip():
        if ch.isalpha():
            if ch.upper() == ch.lower():
                return None
            return ch.isupper()
    return None


def is_organ_statement(text: str) -> bool:
    """An issuing-organ statement, not a mention embedded in ordinary prose."""
    body = " ".join((text or "").split())
    match = _BROAD_ORGAN_STATEMENT.search(body)
    if not match or match.start() > 4:
        return False
    if body.endswith(",") and ";" not in body:
        return True
    coverage = (match.end() - match.start()) / max(1, len(body))
    return (
        coverage >= 0.9
        and len(body.split()) <= 14
        and not re.search(r"[;:.]\s*\d*\s*$", body)
    )


def _organ_identity(text: str) -> str:
    return re.sub(r"\W+", "", (text or "").casefold())


def _is_heading(region: Any) -> bool:
    return region.heading_relation == "IS_HEADING"


def _supplies_list_frame(region: Any) -> bool:
    body = " ".join((region.text or "").split())
    return bool(
        body
        and (
            is_enacting_clause(body)
            or region.proposition_bearing
            or _LIST_FRAME.search(body)
        )
    )


def _is_operational_enacting(text: str) -> bool:
    return bool(_PERFORMATIVE_AT_START.search(text or ""))


def _opens_list(regions: Sequence[Any], position: int) -> bool:
    region = regions[position]
    body = " ".join((region.text or "").split())
    # V581A M5a.  A declared HTML list block is opened by its immediate
    # lead-in when the author marked that lead-in as syntactically open with
    # a trailing colon.  list_item_state is set from DOM ancestry — declared
    # structure — and declared structure may not be vetoed by the verb
    # inventory below (INSTRUMENT_BOUNDARY_CONTRACT §6).  The colon is not a
    # new criterion: it is this function's own list mark, which was
    # unreachable whenever _supplies_list_frame failed first.
    if (position + 1 < len(regions)
            and regions[position + 1].list_item_state == "LIST_ITEM"
            and not _is_heading(region)
            and region.list_item_state != "LIST_ITEM"
            and body.endswith(":")):
        return True
    if not _supplies_list_frame(region):
        return False
    continuation: list[str] = []
    for offset in range(position + 1, min(len(regions), position + 4)):
        next_region = regions[offset]
        next_body = " ".join((next_region.text or "").split())
        definition_label = bool(
            "bezeichnen" in body.casefold()
            and re.fullmatch(r"\d{1,3}\.", next_body)
        )
        next_is_item = (
            next_region.list_item_state == "LIST_ITEM"
            or bool(_ENUMERATED_ITEM.match(next_body))
            or definition_label
            or (
                "bezeichnen" in body.casefold()
                and next_body.startswith(("„", "\"", "«"))
            )
        )
        if next_is_item:
            combined = " ".join([body, *continuation])
            if combined.endswith(":") or body.endswith(":"):
                return True
            return (
                region.dom_or_layout_role == "FLAT_PARAGRAPH"
                and bool(_LIST_FRAME.search(body))
                and not any(item.proposition_bearing
                            for item in regions[position + 1:offset])
            )
        if next_region.proposition_bearing or _is_heading(next_region):
            return False
        continuation.append(next_body)
    return False


def _is_list_member(region: Any, lead: Any) -> bool:
    if region.list_item_state == "LIST_ITEM":
        return True
    if _is_operational_enacting(region.text or ""):
        return False
    if _ENUMERATED_ITEM.match(region.text or ""):
        return True
    return (
        "bezeichnen" in (lead.text or "").casefold()
        and (region.text or "").lstrip().startswith(("„", "\"", "«"))
    )


def _continuation_parent(regions: Sequence[Any], position: int
                         ) -> tuple[Any, int] | None:
    """A single syntactically open parent across at most two non-clauses."""
    target = regions[position]
    if position == 0 or _is_heading(target) or _ENUMERATED_ITEM.match(
            target.text or "") or target.list_item_state == "LIST_ITEM":
        return None
    for gap in range(1, min(3, position) + 1):
        source = regions[position - gap]
        if source.list_item_state == "LIST_ITEM":
            continue
        # V581B M6a.  In PDF a region is an extraction line, not an authored
        # block; a gap-1 declared predecessor that begins its open frame is
        # the parent regardless of line length (M6_CONTRACT §1).  The bound
        # is retained for HTML (block boundaries are authored), for gaps 2-3,
        # and for sources whose tail opens a subordinate frame.
        long_line_waiver = False
        if len(source.text or "") > MAX_GOVERNOR_EVIDENCE_CHARS:
            if not (gap == 1
                    and source.content_provenance == "PDF_TEXT_OPERATORS"
                    and not _SUBORDINATE_TAIL.search(
                        " ".join((source.text or "").split()))):
                continue
            long_line_waiver = True
        if gap > 1 and any(
            item.proposition_bearing
            or is_recital(item.text or "")
            or _is_operational_enacting(item.text or "")
            or _supplies_list_frame(item)
            or _is_heading(item)
            for item in regions[position - gap + 1:position]
        ):
            continue
        body = (source.text or "").strip()
        if not body or _TERMINAL_PUNCTUATION.search(body):
            continue
        target_body = (target.text or "").strip()
        source_has_frame = (
            source.proposition_bearing
            or is_recital(body)
            or _is_operational_enacting(body)
            or bool(_LIST_FRAME.search(body))
            or bool(_LOCAL_FINITE.search(body))
        )
        if long_line_waiver:
            # The waived source must BEGIN its open frame: a long line that
            # starts mid-sentence is itself a fragment, and attaching to it
            # would mis-name the parent.
            source_has_frame = (
                _sentence_initial(body) is True
                or bool(_ENUMERATED_ITEM.match(body))
                or is_recital(body)
            )
        target_continues = (
            bool(_RECITAL_CONTINUATION.match(target_body))
            or (
                gap == 1
                and _PROPER_NAME_CONTINUATION.search(body)
                and _PROPER_NAME_TARGET.match(target_body)
            )
        )
        if long_line_waiver and not target_continues:
            stripped = _LEADING_DIGIT_GROUP.sub("", target_body)
            target_continues = (
                stripped != target_body
                and bool(_RECITAL_CONTINUATION.match(stripped))
            )
        if source_has_frame and target_continues:
            return source, gap
    return None


def _has_explicit_local_frame(region: Any) -> bool:
    body = " ".join((region.text or "").split())
    return bool(
        _COORDINATE_OR_ITEM.match(body)
        or _BIBLIOGRAPHIC_OR_METADATA.search(body)
        or (
            not is_recital(body)
            and _LOCAL_FINITE.search(body)
        )
    )


# ===========================================================================
# §9 — build the context from typed regions
# ===========================================================================

def _lineage_context(*, regions: Sequence[Any], position: int,
                     document_id: str, manifestation_id: str,
                     proposition_id: str,
                     span_offsets: tuple[int, int] | None,
                     reading_order_state: str,
                     authority_context: Any) -> StructuralClauseContext:
    """Materialize and consume declared structural lineage for one region."""
    current = regions[position]
    active_organs: list[Any] = []
    organ_anchor_position: int | None = None
    active_recital = False
    active_list: Any = None
    active_list_has_member = False
    current_heading: Any = None
    heading_ancestors: list[str] = []
    relations: list[StructuralRelation] = []
    governing: list[tuple[str, str, float]] = []
    lead_ins: list[str] = []
    shared_subject: list[str] = []
    shared_predicate: list[str] = []
    enacting: list[str] = []
    parent_ids: list[str] = []
    recital_group = ""
    cross_page_allowed = reading_order_state in {
        "READING_ORDER_ESTABLISHED", "READING_ORDER_RECOVERABLE",
        "READING_ORDER_NOT_APPLICABLE",
    }

    def add(governor: Any, relation_type: str, evidence: str,
            *applicability: str) -> None:
        if governor is None or governor.region_id == current.region_id:
            return
        # V581A M5b.  Nothing pastes governor text — the relation records
        # region identifiers and a fixed evidence string — so a length cap on
        # the governor's own line is not a structural criterion for an edge
        # grounded in a DOM declaration.  It is retained for every other edge:
        # PDF targets never carry declared membership, so the surface M1
        # falsified (long arbitrary text admitted as parent) stays unreachable.
        declared_list_edge = (
            relation_type.startswith("LIST_ITEM_INHERITS_")
            and current.list_item_state == "LIST_ITEM"
        )
        # V581B M6c.  Every E4 edge originates in _continuation_parent, which
        # enforces the frozen selection contract, so the cap here is
        # redundant for E4 and remains binding for E1/E2.
        contracted_edge = (
            declared_list_edge
            or relation_type == "GRAMMATICAL_CONTINUATION_INHERITS_FRAME"
        )
        if not contracted_edge \
                and len(governor.text or "") > MAX_GOVERNOR_EVIDENCE_CHARS:
            return
        relation = StructuralRelation(
            stable_id("v5-8-1-relation", current.region_id,
                      governor.region_id, relation_type),
            relation_type, current.region_id, governor.region_id,
            span_offsets, tuple(current.navigation_ancestry), 1.0,
            tuple(applicability), evidence,
        )
        relations.append(relation)
        governing.append((governor.region_id, relation_type, 1.0))
        parent_ids.append(governor.region_id)

    for scan_position, region in enumerate(regions[:position + 1]):
        if _is_heading(region):
            current_heading = region
            heading_ancestors = [region.region_id]
            active_organs = (
                [region] if is_organ_statement(region.text or "") else []
            )
            organ_anchor_position = (
                scan_position if active_organs else None
            )
            active_recital = False
            active_list = None
            active_list_has_member = False
            if scan_position == position:
                break
            continue

        organ_here = is_organ_statement(region.text or "")
        enacting_here = (
            _is_operational_enacting(region.text or "") and not organ_here
        )
        list_opener_here = _opens_list(regions, scan_position)
        if active_organs and not active_recital \
                and organ_anchor_position is not None \
                and scan_position - organ_anchor_position \
                > MAX_STRUCTURAL_LOOKBACK:
            active_organs = []
            organ_anchor_position = None

        # A completed HTML list cannot leak into a later, unrelated block.
        if active_list is not None and active_list_has_member \
                and active_list.dom_or_layout_role != "FLAT_PARAGRAPH" \
                and region.list_item_state != "LIST_ITEM" \
                and not (
                    "bezeichnen" in (active_list.text or "").casefold()
                    and (
                        (region.text or "").lstrip().startswith(
                            ("„", "\"", "«")
                        )
                        or re.fullmatch(
                            r"\s*\d{1,3}\.\s*", region.text or ""
                        )
                    )
                ) \
                and not list_opener_here:
            active_list = None
            active_list_has_member = False

        if scan_position == position:
            if not cross_page_allowed and position > 0:
                pass
            elif organ_here:
                pass
            elif active_list is not None and _is_list_member(
                    region, active_list):
                add(
                    active_list,
                    "LIST_ITEM_INHERITS_LEAD_IN_SUBJECT",
                    "the target is an explicit member of the active list frame",
                    "SAME_DOCUMENT", "ACTIVE_LIST_BLOCK",
                )
                add(
                    active_list,
                    "LIST_ITEM_INHERITS_LEAD_IN_PREDICATE",
                    "the target is an explicit member of the active list frame",
                    "SAME_DOCUMENT", "ACTIVE_LIST_BLOCK",
                )
                lead_ins.append(active_list.region_id)
                shared_subject.append(active_list.region_id)
                shared_predicate.append(active_list.region_id)
            elif is_recital(region.text or "") and active_organs:
                for organ in active_organs:
                    add(
                        organ,
                        "RECITAL_GOVERNED_BY_ENACTMENT",
                        "the target opens a recital in the declared recital block",
                        "SAME_INSTRUMENT", "ACTIVE_RECITAL_BLOCK",
                    )
                if len(active_organs) == 1:
                    recital_group = stable_id(
                        "v5-8-1-recitalgroup",
                        document_id, active_organs[0].region_id,
                    )
            else:
                continuation = _continuation_parent(regions, scan_position)
                if continuation is not None and active_recital and active_organs:
                    # V581B M6b.  A subjectless participial recital directly
                    # preceded by its organ statement takes that organ's
                    # understood subject (the blind panel's own rationale);
                    # a continuation of it is the recital block's own
                    # continuation, and the organ governs through the E1
                    # fallback below.  A recital with a local finite frame,
                    # or one internal to a longer recital chain, keeps the
                    # continuation edge.
                    _m6_source, _m6_gap = continuation
                    _m6_body = " ".join((_m6_source.text or "").split())
                    _m6_prior = scan_position - _m6_gap - 1
                    if is_recital(_m6_body) \
                            and not _LOCAL_FINITE.search(_m6_body) \
                            and _m6_prior >= 0 \
                            and is_organ_statement(" ".join(
                                (regions[_m6_prior].text or "").split())):
                        continuation = None
                if continuation is not None:
                    source, gap = continuation
                    add(
                        source,
                        "GRAMMATICAL_CONTINUATION_INHERITS_FRAME",
                        "the target continues one syntactically open region",
                        "SAME_SECTION", f"PHYSICAL_GAP_{gap}",
                    )
                elif enacting_here and active_organs:
                    for organ in active_organs:
                        add(
                            organ,
                            "OPERATIVE_CLAUSE_ISSUED_BY_ORGAN",
                            "the operative clause belongs to the active issuing organ",
                            "SAME_INSTRUMENT", "OPERATIVE_BOUNDARY",
                        )
                elif active_recital and active_organs \
                        and not _has_explicit_local_frame(region):
                    for organ in active_organs:
                        add(
                            organ,
                            "RECITAL_GOVERNED_BY_ENACTMENT",
                            "the target belongs to the declared recital block",
                            "SAME_INSTRUMENT", "ACTIVE_RECITAL_BLOCK",
                        )
                    if len(active_organs) == 1:
                        recital_group = stable_id(
                            "v5-8-1-recitalgroup",
                            document_id, active_organs[0].region_id,
                        )
            break

        if organ_here:
            identity = _organ_identity(region.text)
            active_organs = [
                item for item in active_organs
                if _organ_identity(item.text) != identity
            ]
            active_organs.append(region)
            organ_anchor_position = scan_position
            active_recital = False
            active_list = None
            active_list_has_member = False
            continue
        if enacting_here:
            enacting.append(region.region_id)
            active_recital = False
        if is_recital(region.text or "") and active_organs:
            active_recital = True
        if list_opener_here:
            active_list = region
            active_list_has_member = False
        elif active_list is not None and _is_list_member(region, active_list):
            active_list_has_member = True

    relation_key = lambda relation: (
        relation.target_region_id, relation.relation_type,
        relation.source_region_id,
    )
    relations = sorted(
        {relation_key(relation): relation for relation in relations}.values(),
        key=relation_key,
    )
    authority_state = "AUTHORITY_NOT_ESTABLISHED"
    authority_text = authority_region = authority_relation = authority_scope = ""
    if authority_context is not None:
        authority_state, best, _ = authority_context.resolution()
        if best is not None and authority_state in {
            "AUTHORITY_ESTABLISHED", "AUTHORITY_RECOVERABLE",
        }:
            authority_text = best.authority_text
            authority_region = best.region_id
            authority_relation = best.applicability
            authority_scope = authority_context.instrument_scope
            if not governing and is_recital(current.text or ""):
                governing.append((
                    best.region_id, "RECITAL_GOVERNED_BY_ENACTMENT", 0.70
                ))
                parent_ids.append(best.region_id)

    candidate_rows = sorted(
        set(governing), key=lambda item: (item[0], item[1], item[2])
    )

    required: list[str] = []
    if candidate_rows:
        required.append("LEFT_CONTEXT")
    if lead_ins:
        required.append("LEAD_IN")
    if heading_ancestors:
        required.append("HEADING")
    payload = "|".join([
        document_id, current.region_id,
        *[item[0] for item in candidate_rows],
        *[item[1] for item in candidate_rows],
        *lead_ins, *heading_ancestors,
    ])
    return StructuralClauseContext(
        context_id=stable_id("v5-8-1-structctx", document_id,
                             current.region_id),
        document_id=document_id,
        manifestation_id=manifestation_id,
        current_region_id=current.region_id,
        current_proposition_id=proposition_id or current.region_id,
        parent_region_ids=tuple(dict.fromkeys(parent_ids)),
        heading_ancestor_ids=tuple(heading_ancestors),
        section_id=current_heading.region_id if current_heading else "",
        document_part_type=(
            "RECITAL_BLOCK"
            if any(relation.relation_type
                   == "RECITAL_GOVERNED_BY_ENACTMENT"
                   for relation in relations)
            else "OPERATIVE_BLOCK" if enacting_here else "BODY"
        ),
        preceding_proposition_ids=tuple(
            region.region_id
            for region in regions[max(0, position - 5):position]
            if region.proposition_bearing
        ),
        following_proposition_ids=tuple(
            region.region_id for region in regions[position + 1:position + 4]
            if region.proposition_bearing
        ),
        governing_clause_candidates=tuple(item[0] for item in candidate_rows),
        governing_clause_relation_types=tuple(
            item[1] for item in candidate_rows
        ),
        governing_clause_confidences=tuple(
            item[2] for item in candidate_rows
        ),
        shared_subject_candidates=tuple(dict.fromkeys(shared_subject)),
        shared_predicate_candidates=tuple(dict.fromkeys(shared_predicate)),
        recital_group_id=recital_group,
        enacting_clause_ids=tuple(dict.fromkeys(enacting)),
        recital_to_enactment_relations=tuple(
            relation for relation in relations
            if relation.relation_type == "RECITAL_GOVERNED_BY_ENACTMENT"
        ),
        list_lead_in_ids=tuple(dict.fromkeys(lead_ins)),
        table_header_context_ids=(),
        table_row_context_ids=(),
        document_authority_state=authority_state,
        applicable_issuing_authority=authority_text,
        applicable_authority_region_id=authority_region,
        applicable_authority_relation=authority_relation,
        authority_scope=authority_scope,
        reading_order_state=reading_order_state,
        required_context_ids=tuple(dict.fromkeys(required)),
        context_source_hash=sha256([payload]),
        recorded_time=now_utc(),
    )

def build_structural_context(*, regions: Sequence[Any], region_id: str,
                             document_id: str, manifestation_id: str,
                             proposition_id: str = "",
                             span_offsets: tuple[int, int] | None = None,
                             reading_order_state: str =
                             "READING_ORDER_NOT_APPLICABLE",
                             authority_context: Any = None
                             ) -> StructuralClauseContext:
    """Derive the typed context for one region from the region sequence.

    ``regions`` is the output of ``regions.segment`` (optionally retyped).  The
    search is bounded by ``MAX_STRUCTURAL_LOOKBACK`` regions and never leaves the
    current section: an unrestricted document-wide scan would find *a* governing
    clause for every span, which is exactly the false recovery §10 forbids.
    """
    index = {region.region_id: position
             for position, region in enumerate(regions)}
    position = index.get(region_id)
    if position is None:
        raise ValueError(f"region {region_id!r} is not in the supplied sequence")
    return _lineage_context(
        regions=regions, position=position,
        document_id=document_id, manifestation_id=manifestation_id,
        proposition_id=proposition_id, span_offsets=span_offsets,
        reading_order_state=reading_order_state,
        authority_context=authority_context,
    )


def empty_context(*, document_id: str = "", region_id: str = "",
                  reading_order_state: str = "READING_ORDER_NOT_APPLICABLE"
                  ) -> StructuralClauseContext:
    """A context that carries no structure, and says so rather than implying it."""
    return StructuralClauseContext(
        context_id=stable_id("v5-8-1-structctx", document_id or "-",
                             region_id or "-"),
        document_id=document_id, manifestation_id="",
        current_region_id=region_id, current_proposition_id=region_id,
        reading_order_state=reading_order_state,
        context_source_hash=sha256([document_id, region_id]),
        recorded_time=now_utc())


__all__ = [
    "RELATION_TYPES", "READING_ORDER_STATES", "MAX_STRUCTURAL_LOOKBACK",
    "GOVERNING_SEPARATION_MARGIN", "StructuralRelation",
    "StructuralClauseContext", "build_structural_context", "empty_context",
    "is_recital", "is_enacting_clause", "is_organ_header",
]

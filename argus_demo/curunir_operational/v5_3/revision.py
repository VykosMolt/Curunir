"""Explicit revision, replacement and edition relations (contract Section 8).

V5.2 missed a relation the documents stated in words. Five EASA safety
bulletins each carry a sentence of the form "This SIB revises EASA SIB
2022-02R3 dated ...". The dependence classifier reasoned from content overlap
and publisher identity, never read the sentence, and returned a shared
evidence basis where all three reviewers read a confirmed derivation.

Explicit revision language is stronger evidence than any overlap statistic.
This module recognises it directly, in the five languages Curunír supports,
and maps it into the source-origin, dependence, temporal and correction
vocabularies at once — a "revises" sentence is simultaneously a derivation, a
supersession and a temporal relation, and V5.2 had no single place to say so.

Patterns are semantic and general; no campaign, source or fixture appears.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v5_1.models import Record, now_utc, stable_id

REVISION_RELATIONS: tuple[str, ...] = (
    "REVISES", "REPLACES", "SUPERSEDES", "UPDATES", "AMENDS", "CORRECTS",
    "WITHDRAWS", "RETRACTS", "CONSOLIDATES", "NEW_EDITION_OF",
    "CORRIGENDUM_TO", "TRANSLATION_OF", "REPUBLISHED_FROM", "ADAPTED_FROM",
    "BASED_ON",
)

# How each explicit relation projects into the frozen ontologies.
RELATION_PROJECTION: Mapping[str, Mapping[str, str]] = {
    "REVISES":        {"content_relation": "SUPERSEDES", "dependence": "DERIVATIVE_CONFIRMED", "temporal": "SUPERSESSION"},
    "REPLACES":       {"content_relation": "SUPERSEDES", "dependence": "DERIVATIVE_CONFIRMED", "temporal": "SUPERSESSION"},
    "SUPERSEDES":     {"content_relation": "SUPERSEDES", "dependence": "DERIVATIVE_CONFIRMED", "temporal": "SUPERSESSION"},
    "UPDATES":        {"content_relation": "UPDATES",    "dependence": "DERIVATIVE_CONFIRMED", "temporal": "TEMPORAL_UPDATE"},
    "AMENDS":         {"content_relation": "UPDATES",    "dependence": "DERIVATIVE_CONFIRMED", "temporal": "TEMPORAL_UPDATE"},
    "CONSOLIDATES":   {"content_relation": "UPDATES",    "dependence": "DERIVATIVE_CONFIRMED", "temporal": "SUPERSESSION"},
    "NEW_EDITION_OF": {"content_relation": "SUPERSEDES", "dependence": "DERIVATIVE_CONFIRMED", "temporal": "SUPERSESSION"},
    "CORRECTS":       {"content_relation": "CORRECTS",   "dependence": "DERIVATIVE_CONFIRMED", "temporal": "CORRECTION"},
    "CORRIGENDUM_TO": {"content_relation": "CORRECTS",   "dependence": "DERIVATIVE_CONFIRMED", "temporal": "CORRECTION"},
    "WITHDRAWS":      {"content_relation": "RETRACTS",   "dependence": "DERIVATIVE_CONFIRMED", "temporal": "RETRACTION"},
    "RETRACTS":       {"content_relation": "RETRACTS",   "dependence": "DERIVATIVE_CONFIRMED", "temporal": "RETRACTION"},
    "TRANSLATION_OF": {"content_relation": "TRANSLATED_FROM", "dependence": "TRANSLATION_DERIVATIVE", "temporal": "NO_CONFLICT"},
    "REPUBLISHED_FROM": {"content_relation": "REPUBLISHES", "dependence": "SYNDICATION_DERIVATIVE", "temporal": "NO_CONFLICT"},
    "ADAPTED_FROM":   {"content_relation": "DERIVED_FROM", "dependence": "PARTIAL_DEPENDENCE", "temporal": "NO_CONFLICT"},
    "BASED_ON":       {"content_relation": "DERIVED_FROM", "dependence": "PARTIAL_DEPENDENCE", "temporal": "NO_CONFLICT"},
}
assert set(RELATION_PROJECTION) == set(REVISION_RELATIONS)

_VERB_PATTERNS: tuple[tuple[str, str], ...] = (
    ("REVISES", r"revises|revidiert|r[ée]vise|revisa|rivede"),
    ("REPLACES", r"replaces|ersetzt|remplace|reemplaza|sostituisce"),
    ("SUPERSEDES", r"supersedes|l[öo]st\s+\S+\s+ab|abroge|deroga|sostituisce\s+integralmente"),
    ("UPDATES", r"updates|aktualisiert|met\s+[àa]\s+jour|actualiza|aggiorna"),
    ("AMENDS", r"amends|amending|[äa]ndert|modifie|modifiant|modifica"),
    ("CORRECTS", r"corrects|berichtigt|corrige|rectifie|corregge"),
    ("WITHDRAWS", r"withdraws|zieht\s+\S+\s+zur[üu]ck|retire|retira|ritira"),
    ("RETRACTS", r"retracts|widerruft|r[ée]tracte|se\s+retracta"),
    ("CONSOLIDATES", r"consolidates|konsolidiert|consolide|consolida"),
    ("NEW_EDITION_OF", r"new\s+edition\s+of|neue\s+(?:auflage|fassung)\s+von|"
                       r"nouvelle\s+[ée]dition\s+de|nueva\s+edici[óo]n\s+de"),
    ("CORRIGENDUM_TO", r"corrigendum\s+(?:to|zu|de|a)|berichtigung\s+(?:zu|der)|"
                       r"rectificatif\s+(?:au|de)|correcci[óo]n\s+de\s+errores"),
    ("TRANSLATION_OF", r"translation\s+of|[üu]bersetzung\s+von|traduction\s+de|"
                       r"traducci[óo]n\s+de|traduzione\s+di"),
    ("REPUBLISHED_FROM", r"republished\s+from|first\s+published\s+(?:by|in)|"
                         r"[üu]bernommen\s+von|republi[ée]\s+de|republicado\s+de"),
    ("ADAPTED_FROM", r"adapted\s+from|adaptiert\s+(?:von|nach)|adapt[ée]\s+de|"
                     r"adaptado\s+de"),
    ("BASED_ON", r"based\s+(?:up)?on|basiert\s+auf|bas[ée]\s+sur|basado\s+en|"
                 r"basato\s+su"),
)

# What a revision statement points at: a document identifier, an act number,
# or a dated reference.
_TARGET = (
    r"(?P<target>(?:[A-Z]{2,6}[\s-]?(?:No\.?\s*)?[\d][\w./–-]{1,24})"
    r"|(?:\((?:EU|EC|EEC|UE|CE)\)\s*(?:No\.?\s*)?\d{1,4}/\d{2,4})"
    r"|(?:\d{1,4}/\d{2,4})"
    r"|(?:(?:this|the)\s+\w+\s+(?:of|dated)\s+\d{1,2}\s+\w+\s+\d{4}))"
)

_COMPILED = tuple(
    (name, re.compile(
        rf"(?P<subject>[^.\n]{{0,80}}?)\b(?:{verbs})\b[^.\n]{{0,40}}?{_TARGET}",
        re.IGNORECASE))
    for name, verbs in _VERB_PATTERNS)


@dataclass(frozen=True)
class RevisionStatement(Record):
    """One explicit revision relation, read from the document's own words."""

    statement_id: str
    relation: str
    subject_document_id: str
    target_reference: str
    matched_text: str
    span_start: int
    span_end: int
    language: str
    content_relation: str
    dependence_class: str
    temporal_relation: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.relation not in REVISION_RELATIONS:
            raise ValueError(f"unknown revision relation: {self.relation}")
        if not self.target_reference.strip():
            raise ValueError("a revision statement must name what it revises")


def find_revision_statements(text: str, *, document_id: str,
                             language: str = "en",
                             window: int = 6000) -> tuple[RevisionStatement, ...]:
    """Read explicit revision relations out of a document's own text.

    Only the head of a document is scanned: revision statements belong to the
    front matter, and scanning the body would pick up quoted references to
    other acts that this document does not itself revise.
    """
    body = (text or "")[:window]
    found: list[RevisionStatement] = []
    seen: set[tuple[str, str]] = set()
    for relation, pattern in _COMPILED:
        for match in pattern.finditer(body):
            target = " ".join(match.group("target").split())
            key = (relation, target.casefold())
            if key in seen:
                continue
            seen.add(key)
            projection = RELATION_PROJECTION[relation]
            found.append(RevisionStatement(
                stable_id("v5-3-revision", document_id, relation, target),
                relation, document_id, target,
                " ".join(match.group(0).split())[:240],
                match.start(), match.end(), language,
                projection["content_relation"], projection["dependence"],
                projection["temporal"], now_utc()))
    return tuple(found)


def relate_documents(left_text: str, right_text: str, *, left_id: str,
                     right_id: str, left_identifiers: Sequence[str] = (),
                     right_identifiers: Sequence[str] = (),
                     left_language: str = "en", right_language: str = "en"
                     ) -> RevisionStatement | None:
    """Does either document explicitly say it revises the other?

    Explicit revision language beats every overlap statistic, so this runs
    before content comparison in the dependence path.
    """
    def _matches(statement: RevisionStatement, identifiers: Sequence[str]) -> bool:
        target = statement.target_reference.casefold().replace(" ", "")
        for identifier in identifiers:
            normalized = str(identifier).casefold().replace(" ", "")
            if not normalized or len(normalized) < 4:
                continue
            if normalized in target or target in normalized:
                return True
        return False

    for text, document_id, language, identifiers in (
            (left_text, left_id, left_language, right_identifiers),
            (right_text, right_id, right_language, left_identifiers)):
        for statement in find_revision_statements(text, document_id=document_id,
                                                  language=language):
            if _matches(statement, identifiers):
                return statement
    return None


def revision_report(statements: Iterable[RevisionStatement | Mapping[str, Any]]
                    ) -> dict[str, Any]:
    counts: dict[str, int] = {}
    projected: dict[str, int] = {}
    for item in statements:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        relation = str(get("relation"))
        counts[relation] = counts.get(relation, 0) + 1
        dependence = str(get("dependence_class"))
        projected[dependence] = projected.get(dependence, 0) + 1
    return {
        "statements": sum(counts.values()),
        "by_relation": dict(sorted(counts.items())),
        "projected_dependence_classes": dict(sorted(projected.items())),
        "relations_supported": len(REVISION_RELATIONS),
        "relations_observed": len(counts),
    }

"""Semantic extraction capability for V5.1 (contract Section 12).

Closes the verified V5 surface-1 failure modes at mechanism level: noun-phrase
spans typed as claims, truncated numeric/claim spans, polarity/modality/
temporal flattening, duplicate candidates, structural-noise inflation,
low-value explosion and mapping-precision misuse.  Composes frozen v4 records;
never edits them.

Layout annotations are consumed structurally: any object exposing quarantined
regions via ``quarantined_regions`` or ``regions`` (items may be attribute
objects, mappings, or ``(start, end, kind)`` tuples) is accepted, so this
module does not bind to a single layout implementation.

All rules are language-general (en/de/fr/es cue tables plus a deterministic
union fallback); nothing here encodes a campaign, source, or fixture answer.

Research shadow only.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..v4.models import MAPPING_PRECISIONS, ExtractionCandidate, NormalizedDocument
from .layout import LAYOUT_REGION_CLASSES, quarantine_decision
from .models import (
    ADMISSION_STAGES, CANDIDATE_FUNCTIONS, MODALITIES, POLARITIES,
    REQUIRED_CANDIDATE_TYPES, CapabilityOutcome, Record, capability_outcome,
    now_utc, sha256, stable_id,
)

# Section 12 temporal-basis vocabulary (module-owned; not a shared ontology).
TEMPORAL_BASES = (
    "EXPLICIT_DATE", "EXPLICIT_PERIOD", "RELATIVE_RESOLVED",
    "DOCUMENT_DATE_FALLBACK", "UNSTATED",
)

_ROLE_FIELDS = (
    "subject", "predicate", "object_or_value", "actor", "attribution",
    "temporal_scope", "geographic_scope", "value", "unit", "referent",
)

_EXACT_PRECISIONS = frozenset({"EXACT_BYTE", "EXACT_CHARACTER", "EXACT_PAGE_CHARACTER"})
_EXACT_SPAN_TYPES = frozenset({"QUOTATION", "NUMERIC_VALUE", "DATE"})
_MATERIAL_TYPES = frozenset({"CLAIM", "RELATION", "EVENT", "QUOTATION", "CORRECTION", "RETRACTION"})

# Section 12.5 — required semantic roles per candidate type.
_REQUIRED_ROLES: Mapping[str, tuple[str, ...]] = {
    "ENTITY_MENTION": ("subject",),
    "CLAIM": ("subject", "predicate", "object_or_value"),
    "RELATION": ("subject", "predicate", "object_or_value"),
    "EVENT": ("subject", "predicate"),
    "QUOTATION": ("attribution",),
    "DATE": ("temporal_scope",),
    "NUMERIC_VALUE": ("value", "unit", "referent"),
    "CORRECTION": ("predicate",),
    "RETRACTION": ("predicate",),
    "CITATION": (),
    "POLARITY": (), "MODALITY": (), "TEMPORAL_SCOPE": (), "GEOGRAPHIC_SCOPE": (),
}
assert set(_REQUIRED_ROLES) == set(REQUIRED_CANDIDATE_TYPES)

_SUPPORTED_LANGUAGES = ("en", "de", "fr", "es")

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "januar": 1, "februar": 2, "märz": 3, "mai": 5, "juni": 6, "juli": 7,
    "oktober": 10, "dezember": 12,
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7,
    "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
_QUARTER_BOUNDS = {1: ("01-01", "03-31"), 2: ("04-01", "06-30"),
                   3: ("07-01", "09-30"), 4: ("10-01", "12-31")}
_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}

_NEGATION = {
    "en": (r"\bno longer\b", r"\bnot\b", r"n['’]t\b", r"\bnever\b",
           r"\bden(?:ies|ied|y)\b", r"\bno\b(?!\s+longer)"),
    "de": (r"\bnicht mehr\b", r"\bnicht\b", r"\bkein(?:e|en|em|er|es)?\b",
           r"\bnie(?:mals)?\b", r"\bbestreitet\b", r"\bdementiert\b"),
    "fr": (r"\bne\s+\w+\s+(?:pas|plus|jamais)\b",
           r"\bn['’]\w+\s+(?:pas|plus|jamais)\b", r"\bd[ée]ment(?:i|ie)?\b",
           r"\baucun(?:e|s|es)?\b"),
    "es": (r"\bya no\b", r"\bno\b", r"\bnunca\b", r"\bniega\b",
           r"\bning[uú]n[oa]?s?\b", r"\bdesmiente\b"),
}

# (pattern, strip_from_working_text) — prefix cues are stripped before role
# splitting; subject-verb cues are not (the attributor is the subject).
_ATTRIBUTION = {
    "en": ((r"\baccording to\s+([^,.;:]{2,80})", True),
           (r"\b([A-Za-zÀ-ÿ][\w\s'’-]{1,60}?)\s+(?:said|says|stated|announced)\b", False)),
    "de": ((r"\blaut\s+([^,.;:]{2,80})", True),
           (r"\bnach angaben (?:von|der|des)\s+([^,.;:]{2,80})", True),
           (r"\b([A-Za-zÀ-ÿ][\w\s'’-]{1,60}?)\s+(?:sagte|erklärte|teilte mit)\b", False)),
    "fr": ((r"\bselon\s+([^,.;:]{2,80})", True),
           (r"\bd['’]après\s+([^,.;:]{2,80})", True),
           (r"\b([A-Za-zÀ-ÿ][\w\s'’-]{1,60}?)\s+a\s+(?:déclaré|affirmé|indiqué)\b", False)),
    "es": ((r"\bsegún\s+([^,.;:]{2,80})", True),
           (r"\b([A-Za-zÀ-ÿ][\w\s'’-]{1,60}?)\s+(?:dijo|declaró|afirmó)\b", False)),
}

_REPORTED_BARE = {
    "en": (r"\breportedly\b", r"\bit is said\b", r"\bis reported\b"),
    "de": (r"\bberichten zufolge\b", r"\bes heißt\b"),
    "fr": (r"\bselon des informations\b", r"\brapporte-t-on\b"),
    "es": (r"\bse informa\b", r"\bse dice\b", r"\bsegún informes\b"),
}

# Checked in order; the first matching modality wins (most specific first).
# Attribution/reported cues are resolved between ALLEGED and PLANNED.
_MODALITY_CUES: tuple[tuple[str, Mapping[str, tuple[str, ...]], bool], ...] = (
    ("VENDOR_DESCRIBED", {
        "en": (r"\b(?:manufacturer|vendor|supplier)\b[^.]{0,40}?\b(?:says|said|describes|claims|markets)\b",
               r"\bproduct (?:sheet|brochure|datasheet)\b", r"\bmarketing material\b"),
        "de": (r"\bhersteller\b[^.]{0,40}?\b(?:beschreibt|bewirbt|gibt an)\b",
               r"\blaut hersteller\b", r"\bproduktblatt\b"),
        "fr": (r"\bfabricant\b[^.]{0,40}?\b(?:décrit|présente)\b", r"\bselon le fournisseur\b"),
        "es": (r"\bfabricante\b[^.]{0,40}?\b(?:describe|presenta)\b", r"\bsegún el proveedor\b"),
    }, True),
    ("EXERCISED", {
        "en": (r"\bexercise\b", r"\bdrill\b", r"\bsimulation\b"),
        "de": (r"\bübung\b", r"\bmanöver\b"),
        "fr": (r"\bexercice\b", r"\bsimulation\b"),
        "es": (r"\bejercicio\b", r"\bsimulacro\b"),
    }, False),
    ("PILOTED", {
        "en": (r"\bpilot(?:\s+(?:project|programme|program|phase|scheme))?\b", r"\btrial run\b"),
        "de": (r"\bpilot(?:projekt|phase|betrieb)\b", r"\berprobung\b"),
        "fr": (r"\b(?:projet|phase)\s+pilote\b",),
        "es": (r"\b(?:proyecto|fase)\s+piloto\b",),
    }, False),
    ("REQUIRED_BY_LAW", {
        "en": (r"\brequired by law\b", r"\blegally required\b",
               r"\b(?:law|regulation|statute)\s+requires\b"),
        "de": (r"\bgesetzlich vorgeschrieben\b", r"\bgesetzlich verpflichtet\b"),
        "fr": (r"\bexig[ée] par la loi\b", r"\bobligation légale\b", r"\bla loi impose\b"),
        "es": (r"\bexigido por la ley\b", r"\bobligación legal\b"),
    }, False),
    ("ALLEGED", {
        "en": (r"\balleged(?:ly)?\b", r"\baccus(?:ed|es)\b"),
        "de": (r"\bangeblich\b", r"\bmutmaßlich\b", r"\bvorgeworfen\b"),
        "fr": (r"\bprésumé(?:e|s)?\b", r"\bprétendument\b", r"\baccusé(?:e|s)?\b"),
        "es": (r"\bpresunt[oa]s?\b", r"\bsupuestamente\b", r"\bacusad[oa]s?\b"),
    }, False),
    ("PLANNED", {
        "en": (r"\bplans? to\b", r"\bintends? to\b", r"\bis planned\b", r"\bwill\b"),
        "de": (r"\bgeplant\b", r"\bplant\b", r"\bbeabsichtigt\b"),
        "fr": (r"\bprévoit\b", r"\bprévu(?:e|s|es)?\b", r"\benvisage\b"),
        "es": (r"\bplanea\b", r"\bprevé\b", r"\bprevist[oa]s?\b", r"\btiene previsto\b"),
    }, True),
    ("PROPOSED", {
        "en": (r"\bpropos(?:es|ed|al)\b",),
        "de": (r"\bvorgeschlagen\b", r"\bschlägt vor\b"),
        "fr": (r"\bpropos(?:e|é|ée)\b", r"\bproposition\b"),
        "es": (r"\bpropone\b", r"\bpropuest[oa]\b"),
    }, True),
    ("OPERATIONAL", {
        "en": (r"\boperational\b", r"\bin operation\b", r"\bin (?:daily|regular) use\b"),
        "de": (r"\bim einsatz\b", r"\bin betrieb\b"),
        "fr": (r"\ben service\b", r"\bopérationnel(?:le)?\b"),
        "es": (r"\ben funcionamiento\b", r"\ben operación\b", r"\boperativ[oa]\b"),
    }, False),
    ("DEPLOYED", {
        "en": (r"\bdeployed\b", r"\brolled out\b", r"\bput into service\b"),
        "de": (r"\beingesetzt\b", r"\beingeführt\b"),
        "fr": (r"\bdéployé(?:e|s|es)?\b", r"\bmis(?:e)? en œuvre\b"),
        "es": (r"\bdesplegad[oa]s?\b", r"\bpuest[oa] en marcha\b"),
    }, False),
)

_FINITE_LEXICON = {
    "en": {"am", "is", "are", "was", "were", "be", "been", "being", "has",
           "have", "had", "do", "does", "did", "will", "would", "shall",
           "should", "can", "could", "may", "might", "must", "remains",
           "remain", "became", "become", "becomes"},
    "de": {"ist", "sind", "war", "waren", "wird", "werden", "wurde", "wurden",
           "hat", "haben", "hatte", "hatten", "kann", "können", "muss",
           "müssen", "soll", "sollen", "darf", "dürfen", "will", "wollen",
           "bleibt", "bleiben", "gilt", "gelten"},
    "fr": {"est", "sont", "était", "étaient", "sera", "seront", "a", "ont",
           "avait", "avaient", "fut", "furent", "peut", "peuvent", "doit",
           "doivent", "va", "vont", "reste", "restent", "demeure"},
    "es": {"es", "son", "era", "eran", "fue", "fueron", "está", "están",
           "estaba", "estaban", "ha", "han", "había", "habían", "será",
           "serán", "puede", "pueden", "debe", "deben", "va", "van", "hay",
           "sigue", "siguen"},
}

_COMPLEMENT_STARTERS = {
    "en": {"the", "a", "an", "its", "their", "his", "her", "to", "that", "in",
           "on", "with", "by", "as", "into", "at", "for"},
    "de": {"die", "der", "das", "den", "dem", "des", "ein", "eine", "einen",
           "einem", "einer", "im", "in", "an", "auf", "mit", "für", "dass",
           "zu", "seit", "als"},
    "fr": {"le", "la", "les", "un", "une", "des", "du", "que", "à", "en",
           "dans", "par", "pour", "au", "aux", "sur"},
    "es": {"el", "la", "los", "las", "un", "una", "unos", "unas", "que", "a",
           "en", "de", "con", "por", "para", "al"},
}

_INFLECTION = {
    "en": re.compile(r"(?:ed|ies|es|s)$"),
    "de": re.compile(r"(?:tet|ten|te|st|t)$"),
    "fr": re.compile(r"(?:aient|ait|eront|era|ent|it|e)$"),
    "es": re.compile(r"(?:aron|arán|ará|irán|irá|ó|an|en|a|e)$"),
}

_PREDICATE_CONTINUATION = {"not", "no", "to", "been", "being", "longer",
                           "nicht", "mehr", "pas", "plus", "ne", "été"}
_PARTICIPLE_END = re.compile(r"(?:ed|en|t|é|ée|és|ado|ido|ada|ida)$")

_ACTOR_BY = {
    "en": r"\bby\s+([^,.;]{2,60})", "de": r"\b(?:durch|von)\s+([^,.;]{2,60})",
    "fr": r"\bpar\s+([^,.;]{2,60})", "es": r"\bpor\s+([^,.;]{2,60})",
}

_GEO_PREPOSITION = {
    "en": r"\bin\s+", "de": r"\b(?:in|im)\s+", "fr": r"\b(?:en|au|aux|à)\s+",
    "es": r"\ben\s+",
}
# Fixed-phrase nouns after locative prepositions that are never places.
_GEO_STOPWORDS = frozenset(_MONTHS) | {
    "betrieb", "einsatz", "kraft", "service", "operation", "funcionamiento",
    "marcha", "œuvre", "quartal", "q1", "q2", "q3", "q4",
}

_RELATIVE_YEAR = {
    "en": ((r"\bnext year\b", 1), (r"\blast year\b", -1)),
    "de": ((r"\b(?:nächstes|kommendes) jahr\b", 1), (r"\b(?:letztes|vergangenes) jahr\b", -1)),
    "fr": ((r"\bl['’]année prochaine\b", 1), (r"\bl['’]année dernière\b", -1)),
    "es": ((r"\bel próximo año\b", 1), (r"\bel año pasado\b", -1)),
}

_RANGE_CONNECTOR = {
    "en": r"\b(?:to|until|through|and)\b", "de": r"\b(?:bis|und)\b",
    "fr": r"\b(?:à|au|et|jusqu)\b", "es": r"\b(?:a|al|hasta|y)\b",
}

_UNIT_STOPWORDS = (frozenset(_MONTHS)
                   | {"and", "or", "und", "oder", "et", "ou", "y", "o"}
                   | set().union(*_COMPLEMENT_STARTERS.values()))


def _langs(language: str) -> tuple[str, ...]:
    code = (language or "").strip().casefold()[:2]
    if code in _SUPPORTED_LANGUAGES:
        return (code,)
    return _SUPPORTED_LANGUAGES  # deterministic union fallback


def _patterns(table: Mapping[str, tuple], language: str) -> tuple:
    output: list = []
    for code in _langs(language):
        output.extend(table.get(code, ()))
    return tuple(output)


@dataclass(frozen=True)
class SemanticParse(Record):
    """Deterministic rule-based reading of one candidate span.

    ``completeness_missing`` records which semantic fields could not be
    recovered from the span itself; admission decides whether that blocks
    acceptance for the candidate's type (Section 12.5).
    """

    parse_id: str
    subject: str
    predicate: str
    object_or_value: str
    actor: str | None
    polarity: str
    modality: str
    temporal_scope: tuple[str | None, str | None]
    temporal_basis: str
    geographic_scope: tuple[str, ...]
    attribution: str | None
    completeness_missing: tuple[str, ...]
    language: str
    finite_clause: bool
    numeric_values: tuple[str, ...]
    parsed_text_sha256: str

    def __post_init__(self) -> None:
        if self.polarity not in POLARITIES:
            raise ValueError(f"unknown polarity: {self.polarity}")
        if self.modality not in MODALITIES:
            raise ValueError(f"unknown modality: {self.modality}")
        if self.temporal_basis not in TEMPORAL_BASES:
            raise ValueError(f"unknown temporal basis: {self.temporal_basis}")
        if len(self.temporal_scope) != 2:
            raise ValueError("temporal scope must be a (start, end) pair")
        if self.temporal_basis == "UNSTATED" and self.temporal_scope != (None, None):
            raise ValueError("UNSTATED temporal basis cannot carry an invented scope")
        if self.temporal_basis != "UNSTATED" and self.temporal_scope[0] is None:
            raise ValueError("stated temporal basis requires a scope start")
        if self.modality == "ATTRIBUTED" and not (self.attribution or "").strip():
            raise ValueError("ATTRIBUTED modality requires a captured attribution")
        unknown = set(self.completeness_missing) - set(_ROLE_FIELDS)
        if unknown:
            raise ValueError(f"unknown completeness fields: {sorted(unknown)}")
        if not self.language.strip():
            raise ValueError("parse requires a language tag")


def _count_negations(text: str, language: str) -> int:
    pattern = "|".join(f"(?:{value})" for value in _patterns(_NEGATION, language))
    return len(re.findall(pattern, text, flags=re.IGNORECASE)) if pattern else 0


def _capture_attribution(text: str, language: str) -> tuple[str | None, str]:
    """Returns (attributor, working_text with prefix cues removed)."""
    for pattern, strip in _patterns(_ATTRIBUTION, language):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            attributor = " ".join(match.group(1).split()).strip(" '’\"«»")
            working = text
            if strip:
                working = (text[:match.start()] + text[match.end():]).lstrip(" ,;:")
            return attributor, working
    return None, text


def _tokenize(text: str) -> tuple[tuple[str, int], ...]:
    return tuple((m.group(0), m.start()) for m in re.finditer(r"[\w'’À-ÿ-]+", text))


def _is_finite_token(token: str, nxt: str | None, language: str) -> bool:
    folded = token.casefold()
    # Romance clitic contractions (n'a, s'est) hide the finite auxiliary.
    uncontracted = re.sub(r"^(?:n|l|d|s|j|c|m|t)['’]", "", folded)
    for code in _langs(language):
        if folded in _FINITE_LEXICON[code] or uncontracted in _FINITE_LEXICON[code]:
            return True
        if token[:1].islower() and _INFLECTION[code].search(folded):
            if code == "en" and (len(folded) < 4 or folded.endswith(("ss", "us", "is"))):
                continue
            if nxt is not None and nxt.casefold() in _COMPLEMENT_STARTERS[code]:
                return True
    return False


def _split_roles(text: str, language: str) -> tuple[str, str, str, bool]:
    tokens = _tokenize(text)
    finite_at = -1
    for index, (token, _) in enumerate(tokens):
        nxt = tokens[index + 1][0] if index + 1 < len(tokens) else None
        if _is_finite_token(token, nxt, language):
            finite_at = index
            break
    if finite_at < 0:
        subject = " ".join(token for token, _ in tokens).strip()
        return subject, "", "", False
    subject = text[:tokens[finite_at][1]].strip(" ,;:—-–\"'«»()")
    end = finite_at
    while end + 1 < len(tokens):
        token = tokens[end + 1][0]
        folded = token.casefold()
        in_lexicon = any(folded in _FINITE_LEXICON[code] for code in _langs(language))
        if folded in _PREDICATE_CONTINUATION or in_lexicon or (
                token[:1].islower() and _PARTICIPLE_END.search(folded)):
            end += 1
        else:
            break
    predicate_start = tokens[finite_at][1]
    predicate_end = tokens[end][1] + len(tokens[end][0])
    predicate = " ".join(text[predicate_start:predicate_end].split())
    object_part = text[predicate_end:].strip(" ,;:—-–\"'«»().!?…")
    return subject, predicate, object_part, True


def _month_number(word: str) -> int | None:
    return _MONTHS.get(word.casefold())


def _iso(year: int, month: int, day: int) -> str | None:
    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= calendar.monthrange(year, month)[1]):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _year_bounds(year: int) -> tuple[str, str]:
    return f"{year:04d}-01-01", f"{year:04d}-12-31"


def _temporal_matches(text: str) -> list[tuple[int, int, str, str, str]]:
    """All date/period mentions as (start, end, iso_start, iso_end, kind)."""
    found: list[tuple[int, int, str, str, str]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e, *_ in found)

    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            found.append((m.start(), m.end(), iso, iso, "DATE"))
    for m in re.finditer(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", text):
        iso = _iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if iso and free(m.start(), m.end()):
            found.append((m.start(), m.end(), iso, iso, "DATE"))
    day_first = r"\b(\d{1,2})(?:\.|er|º)?(?:\s+de)?\s+([A-Za-zÀ-ÿ]+)(?:\s+de)?\s+(\d{4})\b"
    for m in re.finditer(day_first, text):
        month = _month_number(m.group(2))
        if month is None:
            continue
        iso = _iso(int(m.group(3)), month, int(m.group(1)))
        if iso and free(m.start(), m.end()):
            found.append((m.start(), m.end(), iso, iso, "DATE"))
    for m in re.finditer(r"\b([A-Za-zÀ-ÿ]+)\s+(\d{1,2}),?\s+(\d{4})\b", text):
        month = _month_number(m.group(1))
        if month is None:
            continue
        iso = _iso(int(m.group(3)), month, int(m.group(2)))
        if iso and free(m.start(), m.end()):
            found.append((m.start(), m.end(), iso, iso, "DATE"))
    quarter_patterns = (
        (r"\b[qQ]([1-4])\s*(\d{4})\b", 1, 2), (r"\b(\d{4})\s*[qQ]([1-4])\b", 2, 1),
        (r"\b([1-4])\.\s*[qQ]uartal\s+(\d{4})\b", 1, 2),
        (r"\b([1-4])er?\s+trimestre\s+(?:de\s+)?(\d{4})\b", 1, 2),
    )
    for pattern, qg, yg in quarter_patterns:
        for m in re.finditer(pattern, text):
            if not free(m.start(), m.end()):
                continue
            quarter, year = int(m.group(qg)), int(m.group(yg))
            left, right = _QUARTER_BOUNDS[quarter]
            found.append((m.start(), m.end(), f"{year:04d}-{left}", f"{year:04d}-{right}", "PERIOD"))
    for m in re.finditer(r"\b(first|second|third|fourth)\s+quarter\s+of\s+(\d{4})\b",
                         text, flags=re.IGNORECASE):
        if not free(m.start(), m.end()):
            continue
        quarter, year = _QUARTER_WORDS[m.group(1).casefold()], int(m.group(2))
        left, right = _QUARTER_BOUNDS[quarter]
        found.append((m.start(), m.end(), f"{year:04d}-{left}", f"{year:04d}-{right}", "PERIOD"))
    for m in re.finditer(r"\b([A-Za-zÀ-ÿ]+)\s+(\d{4})\b", text):
        month = _month_number(m.group(1))
        if month is None or not free(m.start(), m.end()):
            continue
        year = int(m.group(2))
        last = calendar.monthrange(year, month)[1]
        found.append((m.start(), m.end(), f"{year:04d}-{month:02d}-01",
                      f"{year:04d}-{month:02d}-{last:02d}", "PERIOD"))
    for m in re.finditer(r"\b((?:19|20)\d{2})\b", text):
        if not free(m.start(), m.end()):
            continue
        left, right = _year_bounds(int(m.group(1)))
        found.append((m.start(), m.end(), left, right, "PERIOD"))
    return sorted(found)


def _parse_temporal(text: str, language: str, document_date: str | None,
                    ) -> tuple[tuple[str | None, str | None], str, tuple[tuple[int, int], ...]]:
    matches = _temporal_matches(text)
    if len(matches) >= 2:
        connector = "|".join(f"(?:{p})" for p in _patterns(
            {k: (v,) for k, v in _RANGE_CONNECTOR.items()}, language))
        between = text[matches[0][1]:matches[1][0]]
        if connector and re.search(connector, between, flags=re.IGNORECASE):
            spans = tuple((s, e) for s, e, *_ in matches)
            # Legal texts cite years in non-chronological order; an interval
            # must never be emitted inverted, so swap to the mentions' true
            # chronological hull when the textual order runs backwards.
            start, end = matches[0][2], matches[1][3]
            if start is not None and end is not None and start > end:
                start, end = matches[1][2], matches[0][3]
            return (start, end), "EXPLICIT_PERIOD", spans
    if matches:
        start, end, iso_start, iso_end, kind = matches[0]
        spans = tuple((s, e) for s, e, *_ in matches)
        basis = "EXPLICIT_DATE" if kind == "DATE" else "EXPLICIT_PERIOD"
        return (iso_start, iso_end), basis, spans
    for pattern, delta in _patterns(_RELATIVE_YEAR, language):
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m and document_date and re.match(r"\d{4}", document_date):
            left, right = _year_bounds(int(document_date[:4]) + delta)
            return (left, right), "RELATIVE_RESOLVED", ((m.start(), m.end()),)
    if document_date:
        return (document_date, document_date), "DOCUMENT_DATE_FALLBACK", ()
    return (None, None), "UNSTATED", ()


def _numeric_signals(text: str, masked: tuple[tuple[int, int], ...],
                     subject: str) -> tuple[tuple[str, ...], bool, bool]:
    """Returns (values, unit_missing, referent_missing) for unconsumed numbers."""
    values: list[str] = []
    unit_missing = referent_missing = False
    for m in re.finditer(r"(?<![\w,.\-])([€$£]\s?)?(\d+(?:[.,]\d+)*)(\s?%)?", text):
        if any(m.start() < e and m.end() > s for s, e in masked):
            continue
        values.append(m.group(0).strip())
        if m.group(1) or m.group(3):
            continue
        following = re.match(r"\s*([A-Za-zÀ-ÿ%€$£]+)", text[m.end():])
        word = following.group(1).casefold() if following else None
        if word is None or word in _UNIT_STOPWORDS:
            unit_missing = True
        preceding = re.findall(r"([A-Za-zÀ-ÿ]{2,})\W+$", text[:m.start()])
        if not subject.strip() and not preceding:
            referent_missing = True
    return tuple(values), unit_missing, referent_missing


def _geographic_scope(text: str, language: str) -> tuple[str, ...]:
    output: list[str] = []
    for code in _langs(language):
        pattern = _GEO_PREPOSITION[code] + r"((?:[A-ZÀ-Þ][\w'’-]+)(?:\s+[A-ZÀ-Þ][\w'’-]+)*)"
        for m in re.finditer(pattern, text):
            place = m.group(1)
            if place.split()[0].casefold() in _GEO_STOPWORDS:
                continue
            if place not in output:
                output.append(place)
    return tuple(output)


def _resolve_modality(text: str, language: str, attributor: str | None,
                      ) -> tuple[str, bool]:
    """Returns (modality, verbal_cue) by fixed precedence."""
    folded = text.casefold()
    specific = ("VENDOR_DESCRIBED", "EXERCISED", "PILOTED", "REQUIRED_BY_LAW", "ALLEGED")
    late = ("PLANNED", "PROPOSED", "OPERATIONAL", "DEPLOYED")
    by_name = {name: (table, verbal) for name, table, verbal in _MODALITY_CUES}
    for name in specific:
        table, verbal = by_name[name]
        if any(re.search(p, folded) for p in _patterns(table, language)):
            return name, verbal
    if attributor is not None:
        return "ATTRIBUTED", True
    if any(re.search(p, folded) for p in _patterns(_REPORTED_BARE, language)):
        return "REPORTED", False
    for name in late:
        table, verbal = by_name[name]
        if any(re.search(p, folded) for p in _patterns(table, language)):
            return name, verbal
    return "ASSERTED", False


def parse_semantics(text: str, context: str, language: str, *,
                    document_date: str | None = None) -> SemanticParse:
    """Deterministic multilingual semantic parse of one span.

    ``context`` is advisory surrounding text; it never silently substitutes
    for span content — missing fields are recorded, and recovery goes through
    ``expand_context`` so the original span is always preserved.
    """
    if not text.strip():
        raise ValueError("cannot parse an empty span")
    negations = _count_negations(text, language)
    polarity = "NEGATIVE" if negations % 2 == 1 else "POSITIVE"
    attributor, working = _capture_attribution(text, language)
    modality, verbal_cue = _resolve_modality(text, language, attributor)
    subject, predicate, object_part, finite = _split_roles(working, language)
    finite = finite or verbal_cue
    temporal_scope, temporal_basis, consumed = _parse_temporal(text, language, document_date)
    numeric_values, unit_missing, referent_missing = _numeric_signals(text, consumed, subject)
    geographic = _geographic_scope(working, language)

    missing: list[str] = []
    if not subject.strip():
        missing.append("subject")
    if not finite or not predicate.strip():
        missing.append("predicate")
    if not finite or not object_part.strip():
        missing.append("object_or_value")
    if numeric_values and unit_missing:
        missing.append("unit")
    if numeric_values and referent_missing:
        missing.append("referent")
    if temporal_basis == "UNSTATED":
        missing.append("temporal_scope")

    actor_match = None
    for pattern in _patterns({k: (v,) for k, v in _ACTOR_BY.items()}, language):
        actor_match = re.search(pattern, working, flags=re.IGNORECASE)
        if actor_match:
            break
    if actor_match:
        actor: str | None = " ".join(actor_match.group(1).split())
    else:
        actor = subject if finite and subject.strip() else None

    # A non-finite span never upgrades beyond ASSERTED-by-default; admission
    # treats its missing predicate as non-propositional content.
    return SemanticParse(
        stable_id("semantic-parse", sha256(text), language, subject, predicate,
                  object_part, polarity, modality),
        subject, predicate, object_part, actor, polarity, modality,
        temporal_scope, temporal_basis, geographic, attributor,
        tuple(missing), language, finite, numeric_values, sha256(text),
    )


_STRUCTURAL_FUNCTIONS = frozenset({"NAVIGATIONAL_METADATA", "DOCUMENT_STRUCTURE"})
_NAVIGATIONAL_KIND_TOKENS = ("PAGE_NUMBER", "PAGINATION", "HEADER", "FOOTER",
                             "NAV", "MASTHEAD", "MENU", "BREADCRUMB")


@dataclass(frozen=True)
class AdmissionResult(Record):
    admission_id: str
    candidate_id: str
    stage: str
    candidate_function: str
    rationale: str
    reclassification_suggestion: str | None
    expansion_request: tuple[str, ...]
    parse_id: str | None
    capability_failure: CapabilityOutcome | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.stage not in ADMISSION_STAGES:
            raise ValueError(f"unknown admission stage: {self.stage}")
        if self.candidate_function not in CANDIDATE_FUNCTIONS:
            raise ValueError(f"unknown candidate function: {self.candidate_function}")
        if not self.rationale.strip():
            raise ValueError("admission requires a rationale")
        if self.stage == "ACCEPTED_CANDIDATE":
            if self.expansion_request:
                raise ValueError("accepted candidate cannot carry an open expansion request")
            if self.candidate_function in _STRUCTURAL_FUNCTIONS | {"DUPLICATE", "NOISE", "UNKNOWN_VALUE"}:
                raise ValueError("accepted candidate requires an analytical function")
            if self.capability_failure is not None:
                raise ValueError("accepted candidate cannot record a capability failure")
        if self.stage == "QUARANTINED" and self.candidate_function not in _STRUCTURAL_FUNCTIONS:
            raise ValueError("quarantine is reserved for structural material")
        if self.capability_failure is not None:
            if self.capability_failure.outcome != "SYSTEM_CAPABILITY_FAILURE":
                raise ValueError("admission may only attach SYSTEM_CAPABILITY_FAILURE outcomes")
            if self.stage != "REJECTED":
                raise ValueError("capability failure must reject the candidate, never accept it")
        if self.reclassification_suggestion is not None:
            if self.reclassification_suggestion not in REQUIRED_CANDIDATE_TYPES:
                raise ValueError("reclassification must target a known candidate type")
            if self.stage != "REJECTED":
                raise ValueError("reclassification is only offered on rejection")
        if self.expansion_request and self.stage != "SEMANTICALLY_PARSED":
            raise ValueError("expansion requests are only open at SEMANTICALLY_PARSED")


def _normalize_type(candidate_type: str) -> str:
    value = candidate_type.strip().upper()
    if value.endswith("_CANDIDATE"):
        value = value[: -len("_CANDIDATE")]
    if value.endswith("_MENTION"):
        value = "ENTITY_MENTION"
    return value


def _quarantined_regions(layout_annotation: Any) -> tuple[tuple[int, int, str], ...]:
    """Structurally read quarantined regions from any layout annotation.

    The canonical shape is v5_1 ``layout.LayoutAnnotation``: ``regions`` holds
    ``(start, end, region_class)`` 3-tuples whose disposition is decided by
    ``layout.quarantine_decision``.  Explicit ``quarantined_regions`` and
    mapping/attribute shapes with their own dispositions remain readable.
    """
    if layout_annotation is None:
        return ()
    regions = getattr(layout_annotation, "quarantined_regions", None)
    implicit = regions is not None
    if regions is None:
        regions = getattr(layout_annotation, "regions", None)
    if regions is None:
        return ()
    if callable(regions):
        regions = regions()
    output: list[tuple[int, int, str]] = []
    for region in regions:
        if isinstance(region, tuple) and len(region) >= 2:
            start, end = int(region[0]), int(region[1])
            kind = str(region[2]).upper() if len(region) > 2 else "STRUCTURE"
            if implicit:
                disposition = "QUARANTINED"
            elif kind in LAYOUT_REGION_CLASSES:
                disposition = quarantine_decision(kind).admission_stage
            else:
                disposition = str(region[3] if len(region) > 3 else "")
        else:
            get = (region.get if isinstance(region, Mapping)
                   else lambda key, default=None, r=region: getattr(r, key, default))
            start = get("start", get("span_start", None))
            end = get("end", get("span_end", None))
            if start is None or end is None:
                continue
            start, end = int(start), int(end)
            kind = str(get("kind", get("region_kind", get("label", "STRUCTURE")))).upper()
            disposition = str(get("disposition", get("state", get("status", ""))))
            if not disposition and kind in LAYOUT_REGION_CLASSES:
                disposition = quarantine_decision(kind).admission_stage
            elif not disposition and implicit:
                disposition = "QUARANTINED"
        if implicit or "QUARANTIN" in disposition.upper():
            output.append((start, end, kind))
    return tuple(sorted(output))


def _structural_function(kind: str) -> str:
    if any(token in kind for token in _NAVIGATIONAL_KIND_TOKENS):
        return "NAVIGATIONAL_METADATA"
    return "DOCUMENT_STRUCTURE"


def _sentence_complete(text: str) -> bool:
    stripped = text.strip()
    first = re.search(r"[A-Za-zÀ-ÿ]", stripped)
    if first is None or not stripped[first.start()].isupper():
        return False
    return stripped.rstrip("\"'»«)]").endswith((".", "!", "?", "…"))


def _role_absent(parse: SemanticParse, role: str) -> bool:
    if role == "attribution":
        return parse.attribution is None
    if role == "value":
        return not parse.numeric_values
    return role in parse.completeness_missing


def _missing_required(parse: SemanticParse, ntype: str) -> tuple[str, ...]:
    required = list(_REQUIRED_ROLES[ntype])
    if ntype in {"CLAIM", "NUMERIC_VALUE"} and parse.numeric_values:
        for extra in ("unit", "referent"):
            if extra not in required:
                required.append(extra)
    return tuple(role for role in required if _role_absent(parse, role))


def _result(candidate: ExtractionCandidate, stage: str, function: str, rationale: str,
            *, reclassification: str | None = None, expansion: tuple[str, ...] = (),
            parse: SemanticParse | None = None,
            failure: CapabilityOutcome | None = None) -> AdmissionResult:
    return AdmissionResult(
        stable_id("admission", candidate.candidate_id, stage, function),
        candidate.candidate_id, stage, function, rationale, reclassification,
        expansion, parse.parse_id if parse else None, failure, now_utc())


def _capability_failure(candidate: ExtractionCandidate, failure_class: str,
                        rationale: str) -> CapabilityOutcome:
    return capability_outcome(
        subject_kind="EXTRACTION_CANDIDATE", subject_id=candidate.candidate_id,
        outcome="SYSTEM_CAPABILITY_FAILURE", rationale=rationale,
        capability_failure_class=failure_class)


def admit_candidate(candidate: ExtractionCandidate, document: NormalizedDocument,
                    layout_annotation: Any, parse: SemanticParse) -> AdmissionResult:
    """Staged admission (Section 12): quarantine, type gate, completeness gate.

    Sufficient-but-uninterpretable evidence becomes SYSTEM_CAPABILITY_FAILURE;
    it is never silently degraded to an unknown state.
    """
    if candidate.document_id != document.document_id:
        raise ValueError("candidate/document provenance mismatch")
    if document.text[candidate.span_start:candidate.span_end] != candidate.original_text:
        raise ValueError("candidate span fabrication or derivative drift")
    if parse.parsed_text_sha256 != sha256(candidate.original_text):
        raise ValueError("semantic parse is not bound to this candidate's span text")

    for start, end, kind in _quarantined_regions(layout_annotation):
        if candidate.span_start < end and candidate.span_end > start:
            return _result(candidate, "QUARANTINED", _structural_function(kind),
                           f"span overlaps layout-quarantined region kind={kind}",
                           parse=parse)

    ntype = _normalize_type(candidate.candidate_type)
    if ntype not in _REQUIRED_ROLES:
        return _result(
            candidate, "REJECTED", "UNKNOWN_VALUE",
            "candidate type is outside the required type ontology", parse=parse,
            failure=_capability_failure(
                candidate, "SEMANTIC_TYPE_ERROR",
                "candidate carries a type the semantic layer cannot represent"))

    if ntype not in {"DATE", "NUMERIC_VALUE"} and not re.search(r"[A-Za-zÀ-ÿ]{2}", candidate.original_text):
        return _result(candidate, "REJECTED", "NOISE",
                       "span contains no linguistic content", parse=parse)

    if ntype == "CLAIM" and not parse.finite_clause:
        return _result(candidate, "REJECTED", "UNKNOWN_VALUE",
                       "noun-phrase span has no propositional content and cannot be a claim",
                       reclassification="ENTITY_MENTION", parse=parse)

    if ntype == "NUMERIC_VALUE" and not parse.numeric_values:
        return _result(
            candidate, "REJECTED", "UNKNOWN_VALUE",
            "numeric candidate without a numeric value", parse=parse,
            failure=_capability_failure(
                candidate, "SEMANTIC_TYPE_ERROR",
                "span typed as numeric value carries no number the parser can bind"))

    missing = _missing_required(parse, ntype)
    if missing:
        if parse.finite_clause and _sentence_complete(candidate.original_text):
            return _result(
                candidate, "REJECTED", "UNKNOWN_VALUE",
                "complete propositional span could not be semantically parsed",
                parse=parse,
                failure=_capability_failure(
                    candidate, "PARSER_INCAPABILITY",
                    "evidence is sufficient (complete finite clause) but required "
                    f"roles {missing} were not recovered"))
        return _result(candidate, "SEMANTICALLY_PARSED", "UNKNOWN_VALUE",
                       "required semantic roles missing from span; context expansion required",
                       expansion=missing, parse=parse)

    if ntype in _EXACT_SPAN_TYPES and candidate.mapping_precision not in _EXACT_PRECISIONS:
        return _result(candidate, "EVIDENCE_BOUND", "UNKNOWN_VALUE",
                       f"{ntype} requires exact span mapping; "
                       f"{candidate.mapping_precision} is insufficient for acceptance",
                       parse=parse)

    function = "ANALYTICALLY_MATERIAL" if ntype in _MATERIAL_TYPES else "SUPPORTING_DETAIL"
    return _result(candidate, "ACCEPTED_CANDIDATE", function,
                   "all required semantic roles recovered from mapped span", parse=parse)


@dataclass(frozen=True)
class ContextExpansion(Record):
    """Bounded, non-destructive context expansion (Section 12.4).

    The original span is persisted verbatim and never replaced; the expansion
    only records what the wider window recovers.
    """

    expansion_id: str
    document_id: str
    original_start: int
    original_end: int
    original_text: str
    expanded_start: int
    expanded_end: int
    expanded_text: str
    reason: str
    boundary_basis: str
    mapping_precision: str
    needed_fields: tuple[str, ...]
    recovered_fields: tuple[str, ...]
    reparse: SemanticParse

    def __post_init__(self) -> None:
        if not (self.expanded_start <= self.original_start
                and self.original_end <= self.expanded_end):
            raise ValueError("expanded window must contain the original span")
        if len(self.expanded_text) != self.expanded_end - self.expanded_start:
            raise ValueError("expanded text does not match its offsets")
        offset = self.original_start - self.expanded_start
        if self.expanded_text[offset:offset + (self.original_end - self.original_start)] != self.original_text:
            raise ValueError("expansion may not replace or alter the original span")
        if not self.needed_fields:
            raise ValueError("expansion requires the fields it is meant to recover")
        if set(self.recovered_fields) - set(self.needed_fields):
            raise ValueError("recovered fields must be a subset of needed fields")
        if self.mapping_precision not in MAPPING_PRECISIONS:
            raise ValueError("invalid mapping precision")
        if not self.reason.strip():
            raise ValueError("expansion requires a reason")


def expand_context(document: NormalizedDocument, span_start: int, span_end: int,
                   needed_fields: tuple[str, ...], max_radius: int = 600, *,
                   document_date: str | None = None) -> ContextExpansion:
    if not 1 <= max_radius <= 5000:
        raise ValueError("expansion radius must be bounded")
    if not (0 <= span_start < span_end <= len(document.text)):
        raise ValueError("span outside document")
    if not needed_fields or set(needed_fields) - set(_ROLE_FIELDS):
        raise ValueError("expansion requires known needed fields")

    text = document.text
    left_limit = max(0, span_start - max_radius)
    left = left_limit
    basis_left = "DOCUMENT_EDGE" if left_limit == 0 else "RADIUS_LIMIT"
    for index in range(span_start - 1, left_limit - 1, -1):
        if text[index] in ".!?\n":
            left = index + 1
            basis_left = "SENTENCE_BOUNDARY"
            break
    right_limit = min(len(text), span_end + max_radius)
    right = right_limit
    basis_right = "DOCUMENT_EDGE" if right_limit == len(text) else "RADIUS_LIMIT"
    for index in range(span_end, right_limit):
        if text[index] in ".!?\n":
            right = index + 1
            basis_right = "SENTENCE_BOUNDARY"
            break

    expanded_text = text[left:right]
    reparse = parse_semantics(expanded_text, "", document.language,
                              document_date=document_date)
    recovered = tuple(field for field in needed_fields
                      if not _role_absent(reparse, field))
    precision = document.mappings[0].precision if document.mappings else "UNMAPPED"
    return ContextExpansion(
        stable_id("context-expansion", document.document_id, span_start, span_end,
                  left, right, needed_fields),
        document.document_id, span_start, span_end, text[span_start:span_end],
        left, right, expanded_text,
        "CONTEXT_EXPANSION_REQUIRED_FOR:" + ",".join(needed_fields),
        f"LEFT_{basis_left}|RIGHT_{basis_right}", precision,
        tuple(needed_fields), recovered, reparse)


def _norm(value: str | None) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", (value or "").casefold()).split())


def deduplicate(candidates_with_parses: Iterable[tuple[ExtractionCandidate, SemanticParse]],
                ) -> dict[str, str]:
    """Semantic dedup (Section 12.6): maps duplicate candidate ids to their
    primary.  Distinct polarity, modality, attribution, or page/section are
    distinct assertions and are preserved."""
    pairs = list(candidates_with_parses)
    for candidate, parse in pairs:
        if parse is None:
            raise ValueError("deduplication requires a semantic parse per candidate")
    ordered = sorted(pairs, key=lambda item: (
        item[0].document_id, item[0].span_start, item[0].span_end, item[0].candidate_id))
    primaries: dict[tuple, str] = {}
    duplicates: dict[str, str] = {}
    for candidate, parse in ordered:
        key = (candidate.source_object_id, candidate.page_or_section,
               _normalize_type(candidate.candidate_type),
               _norm(parse.subject), _norm(parse.predicate),
               _norm(parse.object_or_value), parse.polarity, parse.modality,
               parse.temporal_scope, _norm(parse.attribution))
        if key in primaries:
            duplicates[candidate.candidate_id] = primaries[key]
        else:
            primaries[key] = candidate.candidate_id
    return duplicates

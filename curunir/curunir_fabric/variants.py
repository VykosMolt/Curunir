"""Name variants for search: transliteration and diacritic folding by fixed tables."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Simplified BGN/PCGN romanization. Lossy by nature.
_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "є": "ye", "і": "i", "ї": "yi", "ґ": "g", "ђ": "dj", "ј": "j", "љ": "lj",
    "њ": "nj", "ћ": "c", "џ": "dz",
}

_GREEK_TO_LATIN = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o",
}

# Rough phonetic Latin to Cyrillic; digraphs are replaced first.
_LATIN_TO_CYRILLIC_DIGRAPHS = (
    ("shch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"),
    ("yu", "ю"), ("ya", "я"), ("yo", "ё"), ("ye", "е"),
)
_LATIN_TO_CYRILLIC = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
    "y": "й", "z": "з",
}


def fold_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _map_script(text: str, table: dict[str, str]) -> str:
    out = []
    for ch in text:
        lower = ch.lower()
        mapped = table.get(lower)
        if mapped is None:
            out.append(ch)
        else:
            out.append(mapped.capitalize() if ch.isupper() and mapped else mapped)
    return "".join(out)


def cyrillic_to_latin(text: str) -> str:
    return _map_script(text, _CYRILLIC_TO_LATIN)


def greek_to_latin(text: str) -> str:
    return _map_script(text, _GREEK_TO_LATIN)


def latin_to_cyrillic(text: str) -> str:
    lowered = text
    for digraph, replacement in _LATIN_TO_CYRILLIC_DIGRAPHS:
        lowered = lowered.replace(digraph, replacement).replace(digraph.capitalize(), replacement.upper())
    return _map_script(lowered, _LATIN_TO_CYRILLIC)


def _dominant_script(text: str) -> str:
    counts = {"Latn": 0, "Cyrl": 0, "Grek": 0, "other": 0}
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("CYRILLIC"):
            counts["Cyrl"] += 1
        elif name.startswith("GREEK"):
            counts["Grek"] += 1
        elif name.startswith("LATIN"):
            counts["Latn"] += 1
        else:
            counts["other"] += 1
    return max(counts, key=counts.get) if any(counts.values()) else "Latn"


@dataclass(frozen=True)
class NameVariant:
    family: str      # contracts.QUERY_FAMILIES member
    value: str
    language: str
    script: str
    rationale: str


def name_variants(name: str, *, languages: tuple[str, ...] = (), scripts: tuple[str, ...] = (),
                  aliases: tuple[str, ...] = (),
                  local_labels: tuple[tuple[str, str], ...] = ()) -> tuple[NameVariant, ...]:
    """Query variants for one name. ``local_labels`` are (language, label) pairs."""
    source_script = _dominant_script(name)
    seen: set[str] = set()
    variants: list[NameVariant] = []

    def add(family: str, value: str, language: str, script: str, rationale: str) -> None:
        value = " ".join(value.split())
        if not value or value.casefold() in seen:
            return
        seen.add(value.casefold())
        variants.append(NameVariant(family, value, language, script, rationale))

    add("EXACT_NAME", name, languages[0] if languages else "", source_script, "the name as given")
    add("QUOTED_PHRASE", f'"{name}"', languages[0] if languages else "", source_script,
        "exact-phrase form for full-text engines")
    for alias in aliases:
        add("ALIAS", alias, "", _dominant_script(alias), "caller-supplied alias or former name")
    folded = fold_diacritics(name)
    if folded != name:
        add("TRANSLITERATION", folded, "", "Latn", "diacritics folded")
    if source_script == "Cyrl":
        add("TRANSLITERATION", cyrillic_to_latin(name), "", "Latn", "BGN/PCGN-style romanization")
    elif source_script == "Grek":
        add("TRANSLITERATION", greek_to_latin(name), "", "Latn", "Greek romanization")
    elif source_script == "Latn" and ("Cyrl" in scripts or any(l in ("ru", "uk", "bg", "sr") for l in languages)):
        add("TRANSLITERATION", latin_to_cyrillic(fold_diacritics(name)), "", "Cyrl",
            "approximate phonetic Cyrillic form")
    for language, label in local_labels:
        add("LOCAL_LANGUAGE", label, language, _dominant_script(label),
            f"local-language label ({language}) from evidence")
    return tuple(variants)

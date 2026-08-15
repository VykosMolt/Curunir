"""Human-review packets over dossiers (contract Section 24).

V5.1's Surface-1 conversion failed on all 90 cases, so the human package was
never usable for the surface that most needs human adjudication.  The
conversion here is total by construction: every dossier kind has a plain-
language template, the templates are driven by the same required-section
tables the sufficiency validator uses, and a conversion that cannot fill a
template raises rather than silently emitting a defective packet.

No human study runs.  This module produces packets and protocol only; the
human result count is and stays zero.

Research shadow only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..v4.io import write_json
from ..v5_1.human_package import ConversionFailure, plain_language_lint
from ..v5_1.models import Record, now_utc, sha256, stable_id
from .dossiers import DOSSIER_KINDS, REQUIRED_SECTIONS, Dossier

# What the reviewer is being asked, in plain language, per dossier kind.
DECISION_PROMPTS: Mapping[str, str] = {
    "SPAN_DOSSIER": (
        "You will see a piece of text taken from a public document, the text "
        "around it, and how the system read it. Decide what should happen to "
        "that piece of text."),
    "DOCUMENT_DOSSIER": (
        "You will see how a public document was read into text. Decide whether "
        "the reading is good enough to work from."),
    "SOURCE_DOSSIER": (
        "You will see where a document came from: its web address, the page it "
        "sits on, what it says about itself, and who is named on it. Decide "
        "what role the named organisation played."),
    "SOURCE_PAIR_DOSSIER": (
        "You will see two documents and what they share. Decide whether one "
        "came from the other, whether both came from the same place, or "
        "whether they were put together separately."),
    "CLAIM_EVIDENCE_DOSSIER": (
        "You will see a statement the system wants to make and the evidence it "
        "found. Decide whether the evidence supports the statement."),
    "TEMPORAL_RELATION_DOSSIER": (
        "You will see two statements about the same thing. Decide how they "
        "relate: whether one updates the other, whether they disagree, or "
        "whether they are about different things."),
    "REPORT_PROPOSITION_DOSSIER": (
        "You will see a sentence the system wants to put in a report and what "
        "it is based on. Decide whether the sentence is a fair statement of "
        "the evidence."),
}
assert set(DECISION_PROMPTS) == set(DOSSIER_KINDS)

# Answers every packet offers in addition to its substantive options.
UNIVERSAL_ANSWERS = (
    "EPISTEMICALLY_UNRESOLVABLE",
    "CANNOT_ADJUDICATE",
)

REVIEW_MODES = ("INDEPENDENT_LABEL", "AUDIT_OF_SYSTEM_ANSWER")


@dataclass(frozen=True)
class HumanPacket(Record):
    """One dossier rendered for a person rather than for a model."""

    packet_id: str
    dossier_id: str
    kind: str
    surface: str
    decision_prompt: str
    question: str
    answer_options: tuple[str, ...]
    evidence_sections: Mapping[str, Any]
    original_language_material: Mapping[str, Any]
    translation_material: Mapping[str, Any]
    review_mode: str
    system_answer_shown: bool
    created_time: str
    content_hash: str

    def __post_init__(self) -> None:
        if self.kind not in DECISION_PROMPTS:
            raise ValueError(f"unknown dossier kind: {self.kind}")
        if self.review_mode not in REVIEW_MODES:
            raise ValueError(f"unknown review mode: {self.review_mode}")
        if self.review_mode == "INDEPENDENT_LABEL" and self.system_answer_shown:
            raise ValueError(
                "independent-label mode may never show the system's answer")
        for required in UNIVERSAL_ANSWERS:
            if required not in self.answer_options:
                raise ValueError(f"every human packet must offer {required}")


def convert(dossier: Dossier, *, review_mode: str = "INDEPENDENT_LABEL"
            ) -> HumanPacket:
    """Render one dossier as a human-review packet.

    Raises ``ConversionFailure`` rather than emitting a packet a person could
    not use — the V5.1 conversion emitted nothing at all for Surface 1 and
    recorded 90 capability failures instead.
    """
    if dossier.kind not in DECISION_PROMPTS:
        raise ConversionFailure("PROPOSITION_RENDER_FAILURE",
                                f"no human template for {dossier.kind}")
    missing = [name for name in REQUIRED_SECTIONS[dossier.kind]
               if name not in dossier.sections]
    if missing:
        raise ConversionFailure(
            "CONTEXT_INSUFFICIENT_BY_CONSTRUCTION",
            f"{dossier.kind} is missing sections a person would need: {missing}")

    prompt = DECISION_PROMPTS[dossier.kind]
    findings = plain_language_lint(prompt)
    if findings:
        raise ConversionFailure(
            "PROPOSITION_RENDER_FAILURE",
            f"decision prompt is not plain language: {findings}")

    options = tuple(dict.fromkeys(list(dossier.decision_options) +
                                  list(UNIVERSAL_ANSWERS)))
    provisional = {
        "packet_id": stable_id("v5-2-human-packet", dossier.dossier_id, review_mode),
        "dossier_id": dossier.dossier_id, "kind": dossier.kind,
        "surface": dossier.surface, "decision_prompt": prompt,
        "question": dossier.decision_question, "answer_options": options,
        "evidence_sections": dict(dossier.sections),
        "original_language_material": dict(dossier.original_language_material),
        "translation_material": dict(dossier.translation_material),
        "review_mode": review_mode, "system_answer_shown": False,
        "created_time": now_utc(),
    }
    return HumanPacket(**provisional, content_hash=sha256(provisional))


def build_package(dossiers: Sequence[Dossier], output_root: str | Path
                  ) -> dict[str, Any]:
    """Convert a whole corpus and report conversion failures honestly."""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    packets: list[HumanPacket] = []
    failures: list[dict[str, str]] = []
    for dossier in dossiers:
        try:
            packets.append(convert(dossier))
        except ConversionFailure as exc:
            failures.append({"dossier_id": dossier.dossier_id,
                             "kind": dossier.kind, "reason": str(exc)})
    with (root / "human_packets.jsonl").open("w") as handle:
        for packet in packets:
            handle.write(json.dumps(packet.to_record(), sort_keys=True,
                                    ensure_ascii=False) + "\n")
    by_surface: dict[str, int] = {}
    for packet in packets:
        by_surface[packet.surface] = by_surface.get(packet.surface, 0) + 1
    manifest = {
        "built_time": now_utc(),
        "dossiers_offered": len(dossiers),
        "packets_created": len(packets),
        "human_packet_conversion_failures": len(failures),
        "conversion_failures": failures,
        "per_surface": by_surface,
        "review_modes": list(REVIEW_MODES),
        "human_results_recorded": 0,
        "human_review_state": "HUMAN_REVIEW_PENDING",
        "verdict": "PASS" if not failures else "PARTIAL",
    }
    write_json(root / "human_package_manifest.json", manifest)
    write_json(root / "study_protocol.json", study_protocol())
    return manifest


def study_protocol() -> dict[str, Any]:
    """The protocol a human study would follow.  No study is run."""
    return {
        "protocol_version": "curunir-human-review-v5.2",
        "modes": {
            "INDEPENDENT_LABEL": (
                "The reviewer answers from the evidence alone. The system's "
                "answer is not shown and is not recoverable from the packet."),
            "AUDIT_OF_SYSTEM_ANSWER": (
                "The reviewer is shown the system's answer and asked whether "
                "the evidence in the packet supports it. Used only after an "
                "independent-label pass over the same units."),
        },
        "order": ["INDEPENDENT_LABEL", "AUDIT_OF_SYSTEM_ANSWER"],
        "reviewer_may_always_answer": list(UNIVERSAL_ANSWERS),
        "original_language_shown_separately_from_translation": True,
        "results_recorded": 0,
        "status": "HUMAN_REVIEW_PENDING",
    }

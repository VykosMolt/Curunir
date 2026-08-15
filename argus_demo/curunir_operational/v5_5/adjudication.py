"""V5.5 — dependent adjudication, so the surfaces cannot contradict each other.

The V5.3 panel adjudicated Surface 4 and Surface 6 as independent questions:

    S4  "does the evidence support this claim?"
    S6  "should this proposition be published?"

Of 30 claims adjudicated on both, 15 contradicted, several unanimously on both
sides — the same three reviewers holding 3-0 that the evidence does *not*
support a claim and that the proposition built on it should be published.

They were not being careless.  They were answering two questions against two
standards, and the second question gave them nowhere to put the thought "the
strict support test failed, but this sentence is still fine to print".  The only
expressible answer was ``PUBLISHED``, so that is what they said, and the label
set became self-contradictory.

The repair is not to re-ask the same two questions more carefully.  It is to
make the contradiction *unrepresentable*:

  * Surface 6 is adjudicated **given** the frozen Surface-4 verdict.
  * The dispositions offered are exactly those Section 6.2 permits for that
    support class — so "publish something the support class forbids" is not on
    the form.
  * Disagreement with Surface 4 is still possible, but it is routed as an
    explicit **referral** back to Surface 4 rather than smuggled through a
    publication label.

A referral is not a lost vote.  It is the panel telling us the upstream verdict
is wrong, in the one place where that claim can be acted on.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id
from ..v5_4 import publication as PUB
from ..v5_4 import support as SUP


class AdjudicationViolation(RuntimeError):
    """A dependent question was built or answered in a way that could contradict."""


#: The answer a reviewer may give when they believe the upstream support verdict
#: is itself wrong.  It is deliberately not a publication disposition.
UPSTREAM_REFERRAL = "UPSTREAM_SUPPORT_DISPUTED"

#: Available on every dependent question, regardless of support class.
UNIVERSAL_ANSWERS: tuple[str, ...] = (
    UPSTREAM_REFERRAL,
    "CANNOT_ADJUDICATE",
    "CONSTRUCTION_DEFECT",
)


def permitted_answers(support_class: str) -> tuple[str, ...]:
    """The full answer set a dependent Surface-6 question may offer.

    Exactly the Section 6.2 dispositions for this support class, plus the
    universal escapes.  A reviewer cannot select a factual publication for a
    support class that forbids one, because it is not on the form.
    """
    if support_class not in SUP.SUPPORT_CLASSES:
        raise ValueError(f"unknown support class: {support_class}")
    permitted = PUB.PERMITTED_DISPOSITIONS[support_class]
    return tuple(sorted(permitted)) + UNIVERSAL_ANSWERS


@dataclass(frozen=True)
class DependentQuestion(Record):
    """One Surface-6 question, conditioned on a frozen Surface-4 verdict."""

    question_id: str
    proposition_id: str
    claim_id: str
    support_class: str
    support_assessment_id: str
    support_first_failing_stage: str | None
    wording: str
    allowed_answers: tuple[str, ...]
    question_version: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.support_class not in SUP.SUPPORT_CLASSES:
            raise ValueError(f"unknown support class: {self.support_class}")
        expected = permitted_answers(self.support_class)
        if tuple(self.allowed_answers) != expected:
            raise AdjudicationViolation(
                "a dependent question must offer exactly the dispositions Section "
                f"6.2 permits for {self.support_class} plus the universal escapes; "
                f"got {sorted(self.allowed_answers)}, expected {sorted(expected)}")
        forbidden = set(self.allowed_answers) & PUB.FACTUAL_DISPOSITIONS
        if self.support_class in PUB.NEVER_FACTUAL and forbidden:
            raise AdjudicationViolation(
                f"{self.support_class} may never be published as fact, yet the "
                f"question offers {sorted(forbidden)}")

    @property
    def text(self) -> str:
        return (
            f"The evidence for this claim has been adjudicated as "
            f"{self.support_class}. Given that, is the following wording "
            f"admissible, and in what form?\n\n  {self.wording}\n\n"
            f"If you believe the support adjudication itself is wrong, answer "
            f"{UPSTREAM_REFERRAL} — do not express that view through a "
            f"publication decision.")

    @property
    def identity(self) -> str:
        return sha256({"proposition_id": self.proposition_id,
                       "claim_id": self.claim_id,
                       "support_class": self.support_class,
                       "support_assessment_id": self.support_assessment_id,
                       "wording": self.wording,
                       "question_version": self.question_version})


def build_question(*, proposition_id: str, claim_id: str, support: Any,
                   wording: str,
                   question_version: str = "V5_5_DEPENDENT_1") -> DependentQuestion:
    """Construct a dependent Surface-6 question from a frozen support verdict."""
    if support is None:
        raise AdjudicationViolation(
            "a dependent question requires a frozen Surface-4 verdict; without one "
            "the proposition is REPORT_BLOCKED and there is nothing to adjudicate")
    support_class = str(_get(support, "support_class"))
    return DependentQuestion(
        stable_id("v5-5-dependent", proposition_id, claim_id, support_class,
                  question_version),
        proposition_id, claim_id, support_class,
        str(_get(support, "support_id") or ""),
        _get(support, "first_failing_stage"),
        wording, permitted_answers(support_class), question_version, now_utc())


@dataclass(frozen=True)
class DependentAnswer(Record):
    """One reviewer's answer to a dependent question."""

    answer_id: str
    question_id: str
    seat_id: str
    answer: str
    reasoning: str
    recorded_time: str


def record_answer(question: DependentQuestion, *, seat_id: str, answer: str,
                  reasoning: str) -> DependentAnswer:
    if answer not in question.allowed_answers:
        raise AdjudicationViolation(
            f"{answer!r} is not offered for support class {question.support_class}; "
            f"allowed: {sorted(question.allowed_answers)}")
    if not reasoning.strip():
        raise AdjudicationViolation("a dependent answer requires reasoning")
    return DependentAnswer(
        stable_id("v5-5-answer", question.question_id, seat_id, answer),
        question.question_id, seat_id, answer, reasoning, now_utc())


# ---------------------------------------------------------------------------
# Contradiction is unrepresentable — the property this module exists to hold.
# ---------------------------------------------------------------------------

def contradicts(support_class: str, disposition: str) -> bool:
    """Would this pair have been a V5.3-style cross-surface contradiction?"""
    if support_class in PUB.NEVER_FACTUAL and disposition in PUB.FACTUAL_DISPOSITIONS:
        return True
    if support_class in SUP.AFFIRMATIVE_SUPPORT and disposition == "REJECTED_UNSUPPORTED":
        return True
    return False


def audit_panel(answers: Iterable[DependentAnswer | Mapping[str, Any]],
                questions: Mapping[str, DependentQuestion]) -> dict[str, Any]:
    """Audit a dependent panel pass.

    ``contradictions`` must be zero and is zero by construction: a contradicting
    answer is not on any form.  It is still counted, because a property that is
    only asserted and never measured is a claim rather than a guarantee.
    """
    from collections import Counter
    counts: Counter[str] = Counter()
    referrals: list[str] = []
    contradictions: list[str] = []
    unknown: list[str] = []
    for answer in answers:
        qid = str(_get(answer, "question_id"))
        value = str(_get(answer, "answer"))
        counts[value] += 1
        question = questions.get(qid)
        if question is None:
            unknown.append(qid)
            continue
        if value == UPSTREAM_REFERRAL:
            referrals.append(question.claim_id)
        elif contradicts(question.support_class, value):
            contradictions.append(question.claim_id)
    total = sum(counts.values())
    return {
        "answers": total,
        "answer_distribution": dict(counts),
        "upstream_referrals": len(referrals),
        "referral_rate": round(len(referrals) / total, 4) if total else None,
        "referred_claims": sorted(set(referrals))[:40],
        "contradictions": len(contradictions),
        "answers_for_unknown_questions": len(unknown),
        "verdict": ("PASS" if not contradictions and not unknown else "FAIL"),
        "note": ("a referral is not a lost vote: it is the panel stating that the "
                 "upstream support verdict is wrong, in the one place where that "
                 "claim can be acted on"),
    }


def referral_worklist(audit: Mapping[str, Any],
                      answers: Iterable[DependentAnswer | Mapping[str, Any]],
                      questions: Mapping[str, DependentQuestion]) -> dict[str, Any]:
    """What Surface 4 must re-examine, derived from the referrals."""
    by_claim: dict[str, list[str]] = {}
    for answer in answers:
        if str(_get(answer, "answer")) != UPSTREAM_REFERRAL:
            continue
        question = questions.get(str(_get(answer, "question_id")))
        if question is None:
            continue
        by_claim.setdefault(question.claim_id, []).append(
            str(_get(answer, "reasoning"))[:400])
    return {
        "claims_referred": len(by_claim),
        "claims": [{"claim_id": claim, "seats_referring": len(reasons),
                    "reasoning": reasons} for claim, reasons in sorted(by_claim.items())],
        "rule": ("a claim referred by a majority of seats is re-adjudicated on "
                 "Surface 4 before any Surface-6 number is computed over it"),
    }


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# Retrospective: what the V5.3 contradictions would have become.
# ---------------------------------------------------------------------------

def reclassify_historic(pairs: Sequence[tuple[str, str, str]]) -> dict[str, Any]:
    """Map recorded (claim, S4 label, S6 label) triples onto the new protocol.

    Under dependent adjudication a contradicting S6 answer could not have been
    given, so each historic contradiction becomes one of two things: an answer
    the form still permits, or a referral.  This says which, without inventing
    any reviewer opinion — it only asks whether the recorded answer was on the
    dependent form at all.
    """
    from collections import Counter
    outcomes: Counter[str] = Counter()
    rows = []
    for claim_id, s4, s6 in pairs:
        if s4 not in SUP.SUPPORT_CLASSES:
            outcomes["UNMAPPABLE_SUPPORT_CLASS"] += 1
            continue
        allowed = permitted_answers(s4)
        if s6 in allowed:
            outcome = "EXPRESSIBLE_UNCHANGED"
        else:
            outcome = "WOULD_HAVE_BEEN_A_REFERRAL"
        outcomes[outcome] += 1
        rows.append({"claim_id": claim_id, "s4": s4, "s6": s6, "outcome": outcome,
                     "allowed_under_dependent_protocol": list(allowed)})
    total = sum(outcomes.values())
    return {
        "pairs": total,
        "outcomes": dict(outcomes),
        "contradictions_representable_under_new_protocol": 0,
        "would_have_been_referrals": outcomes["WOULD_HAVE_BEEN_A_REFERRAL"],
        "referral_rate": round(outcomes["WOULD_HAVE_BEEN_A_REFERRAL"] / total, 4)
        if total else None,
        "rows": rows[:40],
        "caveat": ("this maps recorded answers onto the dependent form. it does not "
                   "claim to know what a reviewer would have chosen instead — only "
                   "that the contradicting answer was not available to them"),
    }

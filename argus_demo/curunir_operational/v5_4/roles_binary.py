"""Section 8 — role-targeted binary adjudication of source roles.

V5.3 asked reviewers "which role?" and scored 0.1707.  Two defects produced
that number and only one of them was the classifier.

The first is conceptual: a source is not in exactly one role.  A JRC report is
*authored by* named researchers, *issued by* the JRC, *published by* the
Publications Office and *hosted by* a repository, all at once.  Forcing one
exclusive answer makes role substitution the expected behaviour, and production
duly collapsed everything onto ``PUBLISHED_BY``.

The second was mine: ``build_source_dossier`` ignored ``target_role``, so
fourteen dossiers asking about different roles shared a content hash and asked
reviewers an unanswerable question.  Here the target role participates in the
dossier identity, and a structural test refuses any construction where it does
not.

Each dossier therefore asks exactly one question:

    Does the evidence establish <TARGET_ROLE> between <SOURCE> and <ENTITY>?
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id

# ---------------------------------------------------------------------------
# Section 8.2 — the roles.  All 13 are preserved; none is exclusive.
# ---------------------------------------------------------------------------

SOURCE_ROLES: tuple[str, ...] = (
    "AUTHORED_BY", "EDITED_BY", "SUBMITTED_BY", "ISSUED_BY", "PUBLISHED_BY",
    "HOSTED_BY", "MIRRORED_BY", "ARCHIVED_BY", "TRANSLATED_BY", "SYNDICATED_BY",
    "COMMISSIONED_BY", "OWNED_BY", "OPERATED_BY",
)

#: Section 8.3 — the reviewer's allowed answers.  Binary plus two escapes.
ROLE_LABELS: tuple[str, ...] = (
    "ESTABLISHED", "NOT_ESTABLISHED", "EPISTEMICALLY_UNRESOLVABLE",
    "CONSTRUCTION_DEFECT",
)

#: Section 8.6 — what production may emit.  CONSTRUCTION_DEFECT is a reviewer
#: judgement about the dossier, never a production prediction.
ROLE_PREDICTIONS: tuple[str, ...] = (
    "ESTABLISHED", "NOT_ESTABLISHED", "EPISTEMICALLY_UNRESOLVABLE",
)


class RoleDossierCollision(RuntimeError):
    """Two dossiers asking different role questions share one identity."""


# ---------------------------------------------------------------------------
# Section 8.7 — role-specific evidence requirements.
# ---------------------------------------------------------------------------

#: Observation kinds that on their own establish a role.
#:
#: These names are the vocabulary ``curunir_operational/v5_3/observations.py``
#: actually emits.  An earlier draft of this table used invented kind names and
#: therefore matched nothing: every role resolved to NOT_ESTABLISHED and the
#: whole stream read as dormant.  The emitter is ground truth here.
DECISIVE: Mapping[str, frozenset[str]] = {
    "AUTHORED_BY": frozenset({"EXPLICIT_AUTHOR_LINE"}),
    "EDITED_BY": frozenset({"EXPLICIT_EDITOR_LINE"}),
    "SUBMITTED_BY": frozenset({"EXPLICIT_SUBMISSION_LINE"}),
    "ISSUED_BY": frozenset({"EXPLICIT_ISSUER_LINE", "TITLE_PAGE_INSTITUTION"}),
    "PUBLISHED_BY": frozenset({"EXPLICIT_PUBLISHER_LINE"}),
    "HOSTED_BY": frozenset({"DOMAIN_HOST"}),
    "MIRRORED_BY": frozenset({"MIRROR_NOTICE"}),
    "ARCHIVED_BY": frozenset({"ARCHIVE_PROVIDER"}),
    "TRANSLATED_BY": frozenset({"TRANSLATION_NOTICE"}),
    "SYNDICATED_BY": frozenset({"SYNDICATION_NOTICE"}),
    "COMMISSIONED_BY": frozenset({"COMMISSIONING_NOTICE"}),
    "OWNED_BY": frozenset({"OWNERSHIP_RECORD", "COPYRIGHT_HOLDER"}),
    "OPERATED_BY": frozenset({"OPERATING_ENTITY_NOTICE"}),
}

#: Observation kinds that contribute but never establish a role alone.
SUPPORTING: Mapping[str, frozenset[str]] = {
    "AUTHORED_BY": frozenset({"CONTACT_BLOCK", "FRONT_MATTER"}),
    "EDITED_BY": frozenset({"FRONT_MATTER"}),
    "ISSUED_BY": frozenset({"INSTITUTIONAL_ATTRIBUTION", "DOCUMENT_SERIES_OWNER",
                            "DOCUMENT_IDENTIFIER"}),
    "PUBLISHED_BY": frozenset({"INSTITUTIONAL_ATTRIBUTION", "COPYRIGHT_HOLDER",
                               "DOCUMENT_SERIES_OWNER"}),
    "HOSTED_BY": frozenset({"REDIRECT_CHAIN", "FINAL_URL"}),
    "ARCHIVED_BY": frozenset({"PUBLICATION_DATE"}),
    "SYNDICATED_BY": frozenset({"FOOTER_TEXT"}),
    "OWNED_BY": frozenset({"INSTITUTIONAL_ATTRIBUTION"}),
    "OPERATED_BY": frozenset({"INSTITUTIONAL_ATTRIBUTION"}),
}

#: Section 8.7 — role implications that do NOT hold.  Production may never
#: infer the right-hand role from the left-hand one alone.  This is the
#: host-as-publisher defect, written down so a test can assert it.
PROHIBITED_INFERENCES: tuple[tuple[str, str], ...] = (
    ("HOSTED_BY", "PUBLISHED_BY"),
    ("HOSTED_BY", "AUTHORED_BY"),
    ("HOSTED_BY", "ISSUED_BY"),
    ("ISSUED_BY", "PUBLISHED_BY"),
    ("PUBLISHED_BY", "AUTHORED_BY"),
    ("SUBMITTED_BY", "AUTHORED_BY"),
    ("ARCHIVED_BY", "PUBLISHED_BY"),
    ("MIRRORED_BY", "PUBLISHED_BY"),
    ("SYNDICATED_BY", "AUTHORED_BY"),
    ("OWNED_BY", "OPERATED_BY"),
    ("OPERATED_BY", "OWNED_BY"),
)


@dataclass(frozen=True)
class RoleQuestion(Record):
    """One role question about one (source, entity) pair."""

    question_id: str
    source_id: str
    entity_id: str
    target_role: str
    question_version: str
    evidence_record_ids: tuple[str, ...]
    observation_record_ids: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        if self.target_role not in SOURCE_ROLES:
            raise ValueError(f"unknown source role: {self.target_role}")

    @property
    def text(self) -> str:
        return (f"Does the evidence establish {self.target_role} between "
                f"{self.source_id} and {self.entity_id}?")

    @property
    def identity(self) -> str:
        """Section 8.4 — the target role participates in the content identity."""
        return sha256({
            "source_id": self.source_id,
            "entity_id": self.entity_id,
            "target_role": self.target_role,
            "evidence_record_ids": sorted(self.evidence_record_ids),
            "observation_record_ids": sorted(self.observation_record_ids),
            "question_version": self.question_version,
        })


def role_question(*, source_id: str, entity_id: str, target_role: str,
                  evidence_record_ids: Iterable[str] = (),
                  observation_record_ids: Iterable[str] = (),
                  question_version: str = "V5_4_ROLE_BINARY_1") -> RoleQuestion:
    evidence = tuple(evidence_record_ids)
    observations = tuple(observation_record_ids)
    return RoleQuestion(
        stable_id("v5-4-rolequestion", source_id, entity_id, target_role,
                  question_version, "|".join(sorted(evidence))),
        source_id, entity_id, target_role, question_version, evidence, observations,
        now_utc())


def assert_no_collisions(questions: Sequence[RoleQuestion]) -> dict[str, Any]:
    """Section 8.4 — two dossiers differing in target role may never collide.

    This is the structural test for the V5.3 defect.  It refuses rather than
    reports, because a collision means reviewers were asked a question they
    could not answer and any score computed over it is meaningless.
    """
    by_identity: dict[str, list[RoleQuestion]] = {}
    for question in questions:
        by_identity.setdefault(question.identity, []).append(question)
    collisions = []
    for identity, group in by_identity.items():
        roles = {q.target_role for q in group}
        pairs = {(q.source_id, q.entity_id) for q in group}
        if len(roles) > 1 or len(pairs) > 1:
            collisions.append({"identity": identity, "roles": sorted(roles),
                               "question_ids": [q.question_id for q in group]})
    if collisions:
        raise RoleDossierCollision(
            f"{len(collisions)} identity collision(s) across differing role "
            f"questions: {collisions[:3]}")
    return {"questions": len(questions), "distinct_identities": len(by_identity),
            "target_role_in_identity": True, "collisions": 0, "verdict": "PASS"}


# ---------------------------------------------------------------------------
# Section 8.6 — production prediction, one role at a time.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoleVerdict(Record):
    """A per-role production prediction with its evidence and inference chain."""

    verdict_id: str
    source_id: str
    entity_id: str
    target_role: str
    prediction: str
    evidence_strength: str
    decisive_observations: tuple[str, ...]
    supporting_observations: tuple[str, ...]
    contradictory_observations: tuple[str, ...]
    inference_chain: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        if self.target_role not in SOURCE_ROLES:
            raise ValueError(f"unknown source role: {self.target_role}")
        if self.prediction not in ROLE_PREDICTIONS:
            raise ValueError(f"production may not emit {self.prediction}")
        if self.prediction == "ESTABLISHED" and not self.decisive_observations:
            raise ValueError(
                f"{self.target_role} may not be ESTABLISHED without a decisive "
                "observation; supporting evidence alone is insufficient "
                "(Section 8.7)")


def _kind(observation: Any) -> str:
    """Read an observation's kind under every name the emitter actually uses.

    ``curunir_operational/v5_3/observations.py`` is named in DECISIVE as the
    ground truth for these values, and the field it emits is
    ``observation_type``.  This accessor read only ``kind``/``observation_kind``,
    so every observation the real emitter produced arrived here as the empty
    string, matched no decisive or supporting set, and every role question in
    the stream resolved NOT_ESTABLISHED.  The table was right and unreachable —
    the same failure its own comment records one level further up.
    """
    if isinstance(observation, Mapping):
        return str(observation.get("observation_type") or observation.get("kind")
                   or observation.get("observation_kind") or "")
    return str(getattr(observation, "observation_type", "") or
               getattr(observation, "kind", "") or
               getattr(observation, "observation_kind", ""))


def _entity(observation: Any) -> str:
    if isinstance(observation, Mapping):
        return str(observation.get("entity") or observation.get("observed_value") or "")
    return str(getattr(observation, "entity", "") or
               getattr(observation, "observed_value", ""))


def _obs_id(observation: Any) -> str:
    if isinstance(observation, Mapping):
        return str(observation.get("observation_id") or "")
    return str(getattr(observation, "observation_id", ""))


def predict_role(*, source_id: str, entity_id: str, target_role: str,
                 observations: Sequence[Any],
                 contradictions: Sequence[str] = ()) -> RoleVerdict:
    """Decide one role question from observations, with no cross-role inference.

    A role is ESTABLISHED only on a decisive observation naming this entity.
    Supporting observations raise strength but can never carry the verdict, and
    no other role's evidence is consulted — that is what produced host-as-
    publisher.
    """
    if target_role not in SOURCE_ROLES:
        raise ValueError(f"unknown source role: {target_role}")
    decisive_kinds = DECISIVE.get(target_role, frozenset())
    supporting_kinds = SUPPORTING.get(target_role, frozenset())

    decisive, supporting = [], []
    for observation in observations:
        if _entity(observation) and entity_id and \
                _entity(observation).casefold() not in entity_id.casefold() and \
                entity_id.casefold() not in _entity(observation).casefold():
            continue
        kind = _kind(observation)
        if kind in decisive_kinds:
            decisive.append(_obs_id(observation) or kind)
        elif kind in supporting_kinds:
            supporting.append(_obs_id(observation) or kind)

    chain: list[str] = []
    if decisive:
        prediction = "ESTABLISHED"
        strength = "EXPLICIT" if len(decisive) > 1 else "STRONGLY_IMPLIED"
        chain.append(f"decisive observation(s) for {target_role}: {decisive}")
    elif supporting:
        prediction = "EPISTEMICALLY_UNRESOLVABLE"
        strength = "WEAKLY_IMPLIED"
        chain.append(
            f"only supporting observation(s) present ({supporting}); {target_role} "
            "requires a decisive observation and none was captured")
    else:
        prediction = "NOT_ESTABLISHED"
        strength = "ABSENT"
        chain.append(f"no observation of a kind that can establish {target_role}")
    if contradictions:
        chain.append(f"contradictory observation(s): {list(contradictions)}")
        if prediction == "ESTABLISHED":
            prediction, strength = "EPISTEMICALLY_UNRESOLVABLE", "CONFLICTING"

    for left, right in PROHIBITED_INFERENCES:
        if right == target_role:
            chain.append(f"{left} evidence was not consulted: {left} does not imply "
                         f"{right} (Section 8.7)")

    return RoleVerdict(
        stable_id("v5-4-roleverdict", source_id, entity_id, target_role),
        source_id, entity_id, target_role, prediction, strength,
        tuple(decisive), tuple(supporting), tuple(contradictions), tuple(chain),
        now_utc())


# ---------------------------------------------------------------------------
# Section 8.8 — component diagnostics.
# ---------------------------------------------------------------------------

def component_diagnostics(*, raw_capture: Sequence[tuple[bool, str]] = (),
                          normalization: Sequence[tuple[bool, str]] = (),
                          role_resolution: Sequence[tuple[bool, str]] = (),
                          dossier_rendering: Sequence[tuple[bool, str]] = (),
                          ) -> dict[str, Any]:
    """Report capture, normalization, resolution and rendering separately.

    A single production role score hides which component failed; V5.3 could not
    say whether 0.1707 meant the resolver was wrong or the evidence was never
    captured in the first place.
    """
    def rate(rows: Sequence[tuple[bool, str]]) -> dict[str, Any]:
        total = len(rows)
        ok = sum(1 for good, _ in rows if good)
        return {"total": total, "correct": ok,
                "accuracy": round(ok / total, 4) if total else None,
                "failure_reasons": sorted({r for good, r in rows if not good})[:10]}
    return {
        "raw_evidence_capture_accuracy": rate(raw_capture),
        "normalization_accuracy": rate(normalization),
        "role_resolution_accuracy": rate(role_resolution),
        "dossier_rendering_accuracy": rate(dossier_rendering),
    }


def role_coverage(verdicts: Iterable[RoleVerdict | Mapping[str, Any]]) -> dict[str, Any]:
    """Which of the 13 roles production positively established."""
    established: dict[str, int] = {role: 0 for role in SOURCE_ROLES}
    emitted: dict[str, int] = {role: 0 for role in SOURCE_ROLES}
    for verdict in verdicts:
        role = (verdict.get("target_role") if isinstance(verdict, Mapping)
                else verdict.target_role)
        prediction = (verdict.get("prediction") if isinstance(verdict, Mapping)
                      else verdict.prediction)
        if role in emitted:
            emitted[role] += 1
            if prediction == "ESTABLISHED":
                established[role] += 1
    positive = sum(1 for v in established.values() if v)
    return {"per_role_established": established, "per_role_questions": emitted,
            "roles_with_positive_established": positive,
            "roles_total": len(SOURCE_ROLES),
            "meets_pre_panel_minimum": positive >= 10}

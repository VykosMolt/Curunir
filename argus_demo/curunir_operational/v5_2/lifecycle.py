"""Lifecycle-state and entailment architecture (contract Section 8).

V5.1 carried a flat ``MODALITIES`` vocabulary in which PLANNED, PILOTED,
EXERCISED, DEPLOYED and OPERATIONAL were sibling values of one field with no
ordering and no entailment discipline.  Nothing prevented a claim whose only
evidence was an intention statement from being recorded at, or rendered with,
a stronger state.  The V5.1 full-population audit found 71 such
plan-as-implementation acceptances; three reached the clean held-out corpus.

This module makes lifecycle state first class:

* 24 states arranged as a prerequisite DAG, not a linear sequence, so a state
  entails only what it necessarily presupposes;
* 15 evidence acts, each licensing a maximum state;
* an explicit prohibited-upward-entailment matrix covering Section 8.3;
* deterministic derivation of state and act from five-language cue tables in
  which futurity and modality dominate the state noun, so "soll wieder in
  Betrieb gehen" reads DEPLOYMENT_PLANNED and never OPERATIONAL;
* a report wording guard that refuses a sentence whose lifecycle term is
  stronger than its supporting claim.

All rules are language-general; nothing here encodes a campaign, source or
fixture answer.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..v5_1.models import Record, capability_outcome, now_utc, stable_id

# ---------------------------------------------------------------------------
# Section 8.1 — lifecycle states
# ---------------------------------------------------------------------------

LIFECYCLE_STATES: tuple[str, ...] = (
    "CONCEPTUAL", "PROPOSED", "ANNOUNCED", "POLICY_ADOPTED", "BUDGET_REQUESTED",
    "FUNDED", "PROCUREMENT_PLANNED", "PROCUREMENT_OPEN", "CONTRACT_AWARDED",
    "IN_DEVELOPMENT", "TECHNICALLY_AVAILABLE", "PILOT_PLANNED", "PILOT_ACTIVE",
    "PILOT_COMPLETED", "EXERCISED", "DEPLOYMENT_PLANNED", "DEPLOYED_LIMITED",
    "DEPLOYED", "OPERATIONAL", "SUSPENDED", "CANCELLED", "RETIRED", "UNKNOWN",
)

# The contract's Section 8.3 names INSTITUTIONALLY_DEPLOYED; it is the same
# state as DEPLOYED under a longer name and is accepted as an input alias so
# the prohibition can be stated in the contract's own words.
STATE_ALIASES: Mapping[str, str] = {"INSTITUTIONALLY_DEPLOYED": "DEPLOYED"}

# Lifecycle transitions that end or interrupt a lifecycle rather than
# strengthen it.  They are never compared for strength and never entail an
# active state.
TERMINAL_STATES = frozenset({"SUSPENDED", "CANCELLED", "RETIRED"})

# Direct prerequisites: what a state necessarily presupposes.  Deliberately
# conservative — an edge is present only where the weaker state must have
# obtained for the stronger one to be true.  Domains differ, so this is a DAG
# and not a chain: IN_DEVELOPMENT does not presuppose FUNDED, PILOT_ACTIVE
# does not presuppose CONTRACT_AWARDED, and DEPLOYMENT_PLANNED sits on its own
# branch away from procurement.
_PREREQUISITES: Mapping[str, tuple[str, ...]] = {
    "CONCEPTUAL": (),
    "PROPOSED": ("CONCEPTUAL",),
    "ANNOUNCED": ("CONCEPTUAL",),
    "POLICY_ADOPTED": ("PROPOSED",),
    "BUDGET_REQUESTED": ("PROPOSED",),
    "FUNDED": ("BUDGET_REQUESTED",),
    "PROCUREMENT_PLANNED": ("PROPOSED",),
    "PROCUREMENT_OPEN": ("PROCUREMENT_PLANNED",),
    "CONTRACT_AWARDED": ("PROCUREMENT_OPEN",),
    "IN_DEVELOPMENT": ("CONCEPTUAL",),
    "TECHNICALLY_AVAILABLE": ("IN_DEVELOPMENT",),
    "PILOT_PLANNED": ("CONCEPTUAL",),
    "PILOT_ACTIVE": ("PILOT_PLANNED",),
    "PILOT_COMPLETED": ("PILOT_ACTIVE",),
    "EXERCISED": ("TECHNICALLY_AVAILABLE",),
    "DEPLOYMENT_PLANNED": ("CONCEPTUAL",),
    "DEPLOYED_LIMITED": ("DEPLOYMENT_PLANNED", "TECHNICALLY_AVAILABLE"),
    "DEPLOYED": ("DEPLOYED_LIMITED",),
    "OPERATIONAL": ("DEPLOYED",),
    "SUSPENDED": (),
    "CANCELLED": (),
    "RETIRED": (),
    "UNKNOWN": (),
}
assert set(_PREREQUISITES) == set(LIFECYCLE_STATES)


def normalize_state(state: str) -> str:
    value = (state or "").strip().upper()
    value = STATE_ALIASES.get(value, value)
    if value not in _PREREQUISITES:
        raise ValueError(f"unknown lifecycle state: {state!r}")
    return value


def _closure(state: str) -> frozenset[str]:
    seen: set[str] = set()
    stack = [state]
    while stack:
        current = stack.pop()
        for parent in _PREREQUISITES[current]:
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return frozenset(seen)


PRESUPPOSED: Mapping[str, frozenset[str]] = {
    state: _closure(state) for state in LIFECYCLE_STATES}


def entails(state: str, other: str) -> bool:
    """True when ``state`` being true necessarily makes ``other`` true.

    UNKNOWN entails nothing and is entailed by nothing.  Terminal states
    (SUSPENDED, CANCELLED, RETIRED) interrupt a lifecycle rather than
    strengthen it, so they entail nothing beyond themselves.
    """
    left, right = normalize_state(state), normalize_state(other)
    if "UNKNOWN" in {left, right}:
        return left == right
    if left == right:
        return True
    if left in TERMINAL_STATES:
        return False
    return right in PRESUPPOSED[left]


def stronger_than(state: str, other: str) -> bool:
    """True when ``state`` is a strictly stronger claim than ``other``."""
    left, right = normalize_state(state), normalize_state(other)
    return left != right and entails(left, right)


def comparable(state: str, other: str) -> bool:
    left, right = normalize_state(state), normalize_state(other)
    return entails(left, right) or entails(right, left)


# Section 8.3 — the prohibitions the contract states in full.  They are
# asserted against the DAG at import time so the matrix and the closure can
# never drift apart.
PROHIBITED_UPWARD_ENTAILMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PROPOSED", ("ANNOUNCED", "FUNDED", "CONTRACT_AWARDED", "IN_DEVELOPMENT",
                  "DEPLOYED", "OPERATIONAL")),
    ("ANNOUNCED", ("FUNDED", "CONTRACT_AWARDED", "DEPLOYED", "OPERATIONAL")),
    ("FUNDED", ("CONTRACT_AWARDED", "TECHNICALLY_AVAILABLE", "DEPLOYED",
                "OPERATIONAL")),
    ("CONTRACT_AWARDED", ("DEPLOYED", "OPERATIONAL")),
    ("PILOT_ACTIVE", ("DEPLOYED", "OPERATIONAL")),
    ("EXERCISED", ("OPERATIONAL",)),
    ("TECHNICALLY_AVAILABLE", ("INSTITUTIONALLY_DEPLOYED",)),
)
for _from, _forbidden in PROHIBITED_UPWARD_ENTAILMENTS:
    for _target in _forbidden:
        assert not entails(_from, _target), (
            f"prohibited entailment {_from} -> {_target} is licensed by the DAG")


def entailment_matrix() -> dict[str, dict[str, bool]]:
    """The full state-by-state entailment table, for the artifact record."""
    return {left: {right: entails(left, right) for right in LIFECYCLE_STATES}
            for left in LIFECYCLE_STATES}


# ---------------------------------------------------------------------------
# Section 8.2 — evidence acts
# ---------------------------------------------------------------------------

EVIDENCE_ACTS: tuple[str, ...] = (
    "ACTOR_INTENTION", "POLITICAL_COMMITMENT", "FORMAL_POLICY_DECISION",
    "BUDGET_AUTHORIZATION", "PROCUREMENT_NOTICE", "CONTRACT_AWARD",
    "DEVELOPMENT_ACTIVITY", "TECHNICAL_DEMONSTRATION", "PILOT_EVIDENCE",
    "EXERCISE_EVIDENCE", "DEPLOYMENT_EVIDENCE", "OPERATIONAL_USE_EVIDENCE",
    "SUSPENSION_NOTICE", "CANCELLATION_NOTICE", "RETIREMENT_NOTICE",
)

# The strongest state each act can license on its own.
ACT_MAXIMUM_STATE: Mapping[str, str] = {
    "ACTOR_INTENTION": "PROPOSED",
    "POLITICAL_COMMITMENT": "ANNOUNCED",
    "FORMAL_POLICY_DECISION": "POLICY_ADOPTED",
    "BUDGET_AUTHORIZATION": "FUNDED",
    "PROCUREMENT_NOTICE": "PROCUREMENT_OPEN",
    "CONTRACT_AWARD": "CONTRACT_AWARDED",
    "DEVELOPMENT_ACTIVITY": "IN_DEVELOPMENT",
    "TECHNICAL_DEMONSTRATION": "TECHNICALLY_AVAILABLE",
    "PILOT_EVIDENCE": "PILOT_COMPLETED",
    "EXERCISE_EVIDENCE": "EXERCISED",
    "DEPLOYMENT_EVIDENCE": "DEPLOYED",
    "OPERATIONAL_USE_EVIDENCE": "OPERATIONAL",
    "SUSPENSION_NOTICE": "SUSPENDED",
    "CANCELLATION_NOTICE": "CANCELLED",
    "RETIREMENT_NOTICE": "RETIRED",
}
assert set(ACT_MAXIMUM_STATE) == set(EVIDENCE_ACTS)

# Planning states an act may license even though they are not presupposed by
# the act's own maximum: an intention statement licenses the planning branch
# without licensing anything on the delivery branch.
_ACT_EXTRA_STATES: Mapping[str, tuple[str, ...]] = {
    "ACTOR_INTENTION": ("DEPLOYMENT_PLANNED", "PILOT_PLANNED",
                        "PROCUREMENT_PLANNED", "BUDGET_REQUESTED"),
    "POLITICAL_COMMITMENT": ("PROPOSED", "DEPLOYMENT_PLANNED", "PILOT_PLANNED"),
    "FORMAL_POLICY_DECISION": ("ANNOUNCED", "PROCUREMENT_PLANNED"),
    "BUDGET_AUTHORIZATION": ("PROCUREMENT_PLANNED",),
    "PROCUREMENT_NOTICE": (),
    "CONTRACT_AWARD": (),
    "DEVELOPMENT_ACTIVITY": (),
    "TECHNICAL_DEMONSTRATION": ("PILOT_PLANNED", "DEPLOYMENT_PLANNED"),
    "PILOT_EVIDENCE": ("PILOT_ACTIVE", "TECHNICALLY_AVAILABLE", "IN_DEVELOPMENT",
                       "CONCEPTUAL"),
    "EXERCISE_EVIDENCE": (),
    "DEPLOYMENT_EVIDENCE": (),
    "OPERATIONAL_USE_EVIDENCE": (),
    "SUSPENSION_NOTICE": (),
    "CANCELLATION_NOTICE": (),
    "RETIREMENT_NOTICE": (),
}


def licensed_states(evidence_act: str) -> frozenset[str]:
    act = (evidence_act or "").strip().upper()
    if act not in ACT_MAXIMUM_STATE:
        raise ValueError(f"unknown evidence act: {evidence_act!r}")
    top = ACT_MAXIMUM_STATE[act]
    return frozenset({top} | set(PRESUPPOSED[top]) | set(_ACT_EXTRA_STATES[act]))


def act_licenses(evidence_act: str, state: str) -> bool:
    """UNKNOWN asserts nothing, so every act licenses it."""
    normalized = normalize_state(state)
    if normalized == "UNKNOWN":
        licensed_states(evidence_act)  # validates the act name
        return True
    return normalized in licensed_states(evidence_act)


# ---------------------------------------------------------------------------
# Section 8.5 — lifecycle wording used in reports
# ---------------------------------------------------------------------------

# Surface wording -> the lifecycle state that wording asserts.  Futurity and
# modality cues are matched first (``_PLANNING_CUES``) and, when present,
# demote any delivery wording found in the same sentence.  This is the
# mechanism that reads "soll wieder in Betrieb gehen" as DEPLOYMENT_PLANNED.
_PLANNING_CUES: Mapping[str, tuple[str, ...]] = {
    "en": (r"\bplans?\s+to\b", r"\bplanned\s+to\b", r"\bis\s+to\b", r"\bare\s+to\b",
           r"\bwill\s+(?:be\s+)?\w+", r"\bintends?\s+to\b", r"\bintended\s+to\b",
           r"\bexpects?\s+to\b", r"\bis\s+expected\s+to\b", r"\bare\s+expected\s+to\b",
           r"\bset\s+to\b", r"\bdue\s+to\s+(?:be\s+)?\w+", r"\bshould\s+\w+",
           r"\bwould\s+\w+", r"\baims?\s+to\b", r"\bseeks?\s+to\b",
           r"\bproposes?\s+to\b", r"\bfrom\s+20\d\d\s+on(?:wards?)?\b"),
    "de": (r"\bsoll(?:en|te|ten)?\b", r"\bwird\s+\w+en\b", r"\bwerden\s+\w+en\b",
           r"\bplant\b", r"\bgeplant\b", r"\bvorgesehen\b", r"\bbeabsichtigt\b",
           r"\bk[üu]e?nftig\b", r"\bzuk[üu]e?nftig\b", r"\bab\s+20\d\d\b", r"\bwill\s+\w+en\b"),
    "fr": (r"\bpr[ée]voit\s+de\b", r"\bpr[ée]vu\b", r"\bdoit\s+\w+", r"\bdevrait\b",
           r"\benvisage\b", r"\bcompte\s+\w+er\b", r"\bva\s+\w+er\b",
           r"\bsera\b", r"\bseront\b", r"\bà\s+partir\s+de\s+20\d\d\b"),
    "es": (r"\bprev[ée]\b", r"\bprevisto\b", r"\bplanea\b", r"\bpretende\b",
           r"\bdeber[íi]a\b", r"\bser[áa]\b", r"\bser[áa]n\b", r"\bva\s+a\s+\w+",
           r"\ba\s+partir\s+de\s+20\d\d\b"),
}

# Delivery/state wording -> asserted state.  Order matters: the first match in
# this tuple wins, so the strongest and most specific wording is checked first.
_STATE_CUES: tuple[tuple[str, Mapping[str, tuple[str, ...]]], ...] = (
    ("OPERATIONAL", {
        "en": (r"\b(?:is|are|was|were)\s+(?:\w+\s+){0,2}operational\b",
               r"\bin\s+operation\b", r"\boperates?\b",
               r"\bhas\s+been\s+operating\b", r"\bentered\s+(?:into\s+)?service\b",
               r"\bin\s+operational\s+use\b", r"\bused\s+operationally\b",
               r"\bre(?:turned|stored)\s+to\s+(?:operation|service)\b",
               r"\bback\s+in\s+(?:operation|service)\b",
               r"\bresumed\s+(?:operation|service)\b"),
        "de": (r"\bin\s+betrieb\b", r"\bim\s+einsatz\b", r"\bbetreibt\b",
               r"\bim\s+regelbetrieb\b", r"\bwieder\s+aufgenommen\b"),
        "fr": (r"\ben\s+service\b", r"\bexploite\b", r"\bop[ée]rationnel(?:le)?\b"),
        "es": (r"\ben\s+funcionamiento\b", r"\bopera\b", r"\boperativ[oa]\b",
               r"\ben\s+servicio\b"),
    }),
    ("DEPLOYED", {
        "en": (r"\bhas\s+deployed\b", r"\bhave\s+deployed\b", r"\bis\s+deployed\b",
               r"\bare\s+deployed\b", r"\bwas\s+deployed\b", r"\brolled\s+out\b",
               r"\bdeployment\s+(?:is\s+)?complete\b"),
        "de": (r"\bausgerollt\b", r"\bflächendeckend\s+eingeführt\b",
               r"\bhat\s+.{0,40}\beingeführt\b"),
        "fr": (r"\bd[ée]ploy[ée]e?s?\b", r"\bmis\s+en\s+place\b"),
        "es": (r"\bdesplegad[oa]s?\b", r"\bimplantad[oa]s?\b"),
    }),
    ("DEPLOYED_LIMITED", {
        "en": (r"\bdeployed\s+(?:at|in|across)\s+(?:selected|a\s+limited|some)\b",
               r"\blimited\s+deployment\b", r"\bpartial\s+roll[- ]?out\b"),
        "de": (r"\bteilweise\s+eingeführt\b", r"\bin\s+ausgewählten\b"),
        "fr": (r"\bd[ée]ploiement\s+limit[ée]\b",),
        "es": (r"\bdespliegue\s+limitado\b",),
    }),
    ("EXERCISED", {
        "en": (r"\bduring\s+(?:an?\s+)?exercise\b", r"\bexercised\b", r"\bwar\s?game\b",
               r"\btested\s+during\b"),
        "de": (r"\bbei\s+(?:einer\s+)?[üu]e?bung\b", r"\berprobt\b"),
        "fr": (r"\blors\s+d[e']\s?un\s+exercice\b", r"\bexerc[ée]\b"),
        "es": (r"\bdurante\s+(?:un\s+)?ejercicio\b",),
    }),
    ("PILOT_COMPLETED", {
        "en": (r"\bpilot\s+(?:has\s+)?(?:been\s+)?completed\b", r"\bcompleted\s+(?:a\s+|the\s+)?pilot\b",
               r"\bpilot\s+concluded\b"),
        "de": (r"\bpilot(?:projekt|phase)?\s+abgeschlossen\b",),
        "fr": (r"\bpilote\s+achev[ée]\b", r"\bexp[ée]rimentation\s+termin[ée]e\b"),
        "es": (r"\bpiloto\s+(?:ha\s+)?(?:sido\s+)?complet[oa]d[oa]\b",),
    }),
    ("PILOT_ACTIVE", {
        "en": (r"\bpilot\s+(?:is\s+)?(?:currently\s+)?(?:running|under\s?way|ongoing|active)\b",
               r"\bis\s+piloting\b", r"\bare\s+piloting\b", r"\bpilot\s+phase\b",
               r"\bin\s+a\s+pilot\b"),
        "de": (r"\bpilotphase\b", r"\bim\s+pilotbetrieb\b", r"\berprobungsphase\b"),
        "fr": (r"\bphase\s+pilote\b", r"\ben\s+exp[ée]rimentation\b"),
        "es": (r"\bfase\s+piloto\b", r"\ben\s+pruebas\b"),
    }),
    ("PILOT_PLANNED", {
        "en": (r"\bpilot\s+(?:is\s+)?planned\b", r"\bwill\s+pilot\b"),
        "de": (r"\bpilot(?:projekt)?\s+(?:ist\s+)?geplant\b",),
        "fr": (r"\bpilote\s+pr[ée]vu\b",),
        "es": (r"\bpiloto\s+previsto\b",),
    }),
    ("TECHNICALLY_AVAILABLE", {
        "en": (r"\bis\s+(?:now\s+)?available\b", r"\bgenerally\s+available\b",
               r"\bdemonstrated\b", r"\bproof\s+of\s+concept\b", r"\bprototype\s+(?:is\s+)?ready\b"),
        "de": (r"\bverfügbar\b", r"\bdemonstriert\b", r"\bprototyp\b"),
        "fr": (r"\bdisponible\b", r"\bd[ée]montr[ée]\b", r"\bprototype\b"),
        "es": (r"\bdisponible\b", r"\bdemostrad[oa]\b", r"\bprototipo\b"),
    }),
    ("IN_DEVELOPMENT", {
        "en": (r"\bunder\s+development\b", r"\bis\s+developing\b", r"\bare\s+developing\b",
               r"\bbeing\s+built\b", r"\bunder\s+construction\b"),
        "de": (r"\bin\s+entwicklung\b", r"\bentwickelt\s+derzeit\b", r"\bim\s+bau\b"),
        "fr": (r"\ben\s+cours\s+de\s+d[ée]veloppement\b", r"\ben\s+construction\b"),
        "es": (r"\ben\s+desarrollo\b", r"\ben\s+construcci[óo]n\b"),
    }),
    ("CONTRACT_AWARDED", {
        "en": (r"\bcontract\s+(?:was\s+)?awarded\b", r"\bawarded\s+(?:the\s+|a\s+)?contract\b",
               r"\bsigned\s+(?:the\s+|a\s+)?contract\b", r"\bcontract\s+signature\b"),
        "de": (r"\bzuschlag\s+erhalten\b", r"\bauftrag\s+(?:wurde\s+)?vergeben\b",
               r"\bvertrag\s+unterzeichnet\b"),
        "fr": (r"\bmarch[ée]\s+attribu[ée]\b", r"\bcontrat\s+sign[ée]\b"),
        "es": (r"\bcontrato\s+adjudicado\b", r"\badjudic[óo]\s+el\s+contrato\b"),
    }),
    ("PROCUREMENT_OPEN", {
        "en": (r"\bcall\s+for\s+tenders?\b", r"\btender\s+(?:is\s+)?open\b",
               r"\binvitation\s+to\s+tender\b", r"\bcall\s+(?:is\s+)?open\b",
               r"\bopened\s+a\s+call\b"),
        "de": (r"\bausschreibung\s+(?:ist\s+)?er[öo]e?ffnet\b", r"\blaufende\s+ausschreibung\b"),
        "fr": (r"\bappel\s+d['’]offres\b",),
        "es": (r"\blicitaci[óo]n\s+abierta\b", r"\bconvocatoria\s+abierta\b"),
    }),
    ("PROCUREMENT_PLANNED", {
        "en": (r"\bprocurement\s+(?:is\s+)?planned\b", r"\bprior\s+information\s+notice\b"),
        "de": (r"\bvergabe\s+(?:ist\s+)?geplant\b",),
        "fr": (r"\bavis\s+de\s+pr[ée][- ]?information\b",),
        "es": (r"\banuncio\s+previo\b",),
    }),
    ("FUNDED", {
        "en": (r"\bhas\s+(?:been\s+)?allocated\b", r"\bfunding\s+(?:was\s+|has\s+been\s+)?(?:approved|secured|granted)\b",
               r"\bbudget\s+(?:was\s+)?adopted\b", r"\bis\s+funded\b", r"\bawarded\s+.{0,30}\bgrant\b"),
        "de": (r"\bmittel\s+(?:wurden\s+)?bewilligt\b", r"\bfinanziert\b",
               r"\bhaushalt\s+beschlossen\b"),
        "fr": (r"\bfinancement\s+(?:a\s+[ée]t[ée]\s+)?(?:approuv[ée]|accord[ée])\b",
               r"\bcr[ée]dits\s+allou[ée]s\b"),
        "es": (r"\bfinanciaci[óo]n\s+aprobada\b", r"\bfondos\s+asignados\b"),
    }),
    ("BUDGET_REQUESTED", {
        "en": (r"\brequested\s+.{0,30}\bbudget\b", r"\bbudget\s+request\b",
               r"\bhas\s+asked\s+for\s+.{0,30}\bfunding\b"),
        "de": (r"\bmittel\s+beantragt\b", r"\bhaushaltsantrag\b"),
        "fr": (r"\bdemande\s+de\s+cr[ée]dits\b",),
        "es": (r"\bsolicitud\s+de\s+presupuesto\b",),
    }),
    ("POLICY_ADOPTED", {
        "en": (r"\badopted\s+(?:the\s+|a\s+)?(?:regulation|directive|decision|strategy|policy|conclusions)\b",
               r"\bentered\s+into\s+force\b", r"\bwas\s+enacted\b", r"\bcame\s+into\s+force\b"),
        "de": (r"\bverabschiedet\b", r"\bin\s+kraft\s+getreten\b", r"\bbeschlossen\b"),
        "fr": (r"\badopt[ée]\b", r"\bentr[ée]e?\s+en\s+vigueur\b"),
        "es": (r"\badoptad[oa]\b", r"\bentr[óo]\s+en\s+vigor\b"),
    }),
    ("ANNOUNCED", {
        "en": (r"\bannounced\b", r"\bunveiled\b", r"\bpresented\s+plans\b",
               r"\bcommitted\s+to\b"),
        "de": (r"\bangek[üu]e?ndigt\b", r"\bvorgestellt\b", r"\bbekannt\s+gegeben\b"),
        "fr": (r"\bannonc[ée]\b", r"\bd[ée]voil[ée]\b"),
        "es": (r"\banunci[óo]\b", r"\bpresent[óo]\b"),
    }),
    ("PROPOSED", {
        "en": (r"\bproposed\b", r"\bproposal\s+for\b", r"\brecommends?\b",
               r"\bhas\s+put\s+forward\b"),
        "de": (r"\bvorgeschlagen\b", r"\bvorschlag\s+für\b", r"\bempfiehlt\b"),
        "fr": (r"\bpropos[ée]\b", r"\bproposition\s+de\b", r"\brecommande\b"),
        "es": (r"\bpropuest[oa]\b", r"\bpropone\b", r"\brecomienda\b"),
    }),
    ("SUSPENDED", {
        "en": (r"\bsuspended\b", r"\bpaused\b", r"\bput\s+on\s+hold\b"),
        "de": (r"\bausgesetzt\b", r"\bgestoppt\b"),
        "fr": (r"\bsuspendu\b",), "es": (r"\bsuspendid[oa]\b",),
    }),
    ("CANCELLED", {
        "en": (r"\bcancell?ed\b", r"\bscrapped\b", r"\babandoned\b"),
        "de": (r"\babgesagt\b", r"\beingestellt\b", r"\bgestrichen\b"),
        "fr": (r"\bannul[ée]\b", r"\babandonn[ée]\b"),
        "es": (r"\bcancelad[oa]\b", r"\babandonad[oa]\b"),
    }),
    ("RETIRED", {
        "en": (r"\bretired\b", r"\bdecommissioned\b", r"\btaken\s+out\s+of\s+service\b",
               r"\bwithdrawn\s+from\s+service\b"),
        "de": (r"\bstillgelegt\b", r"\bau[ßs]er\s+betrieb\s+genommen\b"),
        "fr": (r"\bd[ée]class[ée]\b", r"\bretir[ée]\s+du\s+service\b"),
        "es": (r"\bretirad[oa]\s+del\s+servicio\b", r"\bdado\s+de\s+baja\b"),
    }),
)

# Delivery verbs in infinitive or gerund form.  On their own they assert
# nothing, but under a planning cue ("plans to deploy", "prévoit de déployer")
# they name which planning branch the intention sits on.  Consulted only when
# a planning cue fired and no finite delivery wording matched.
_PLANNED_OBJECT_CUES: tuple[tuple[str, Mapping[str, tuple[str, ...]]], ...] = (
    ("DEPLOYMENT_PLANNED", {
        "en": (r"\bdeploy(?:ing|ment)?\b", r"\broll(?:ing)?\s+out\b",
               r"\bput\s+into\s+(?:service|operation)\b", r"\bgo\s+live\b",
               r"\bmake\s+operational\b", r"\bbring\s+into\s+service\b"),
        "de": (r"\bein(?:zu)?führen\b", r"\bausrollen\b", r"\bin\s+betrieb\b",
               r"\bin\s+dienst\s+stellen\b"),
        "fr": (r"\bd[ée]ployer\b", r"\bmettre\s+en\s+service\b"),
        "es": (r"\bdesplegar\b", r"\bponer\s+en\s+(?:servicio|funcionamiento)\b"),
    }),
    ("PILOT_PLANNED", {
        "en": (r"\bpilot(?:ing)?\b", r"\btrial\b"),
        "de": (r"\bpilotieren\b", r"\berproben\b"),
        "fr": (r"\bexp[ée]rimenter\b",), "es": (r"\bpilotar\b", r"\bprobar\b"),
    }),
    ("PROCUREMENT_PLANNED", {
        "en": (r"\bprocure(?:ment)?\b", r"\btender\b", r"\bcall\s+for\s+proposals\b"),
        "de": (r"\bbeschaffen\b", r"\bausschreiben\b"),
        "fr": (r"\bacqu[ée]rir\b", r"\bappel\s+d['’]offres\b"),
        "es": (r"\blicitar\b", r"\badquirir\b"),
    }),
    ("BUDGET_REQUESTED", {
        "en": (r"\bfund(?:ing)?\b", r"\binvest(?:ment)?\b", r"\ballocate\b"),
        "de": (r"\bfinanzieren\b", r"\binvestieren\b"),
        "fr": (r"\bfinancer\b", r"\binvestir\b"),
        "es": (r"\bfinanciar\b", r"\binvertir\b"),
    }),
    ("IN_DEVELOPMENT", {
        "en": (r"\bdevelop(?:ing|ment)?\b", r"\bbuild(?:ing)?\b"),
        "de": (r"\bentwickeln\b", r"\bbauen\b"),
        "fr": (r"\bd[ée]velopper\b", r"\bconstruire\b"),
        "es": (r"\bdesarrollar\b", r"\bconstruir\b"),
    }),
)

# A structured status annotation such as "(status: operational)" is an
# explicit assertion of state by the renderer, not prose.  Planning cues in
# the surrounding sentence never demote it, because the annotation is not a
# reading of the sentence — it is a separate claim about the object.
_STATUS_ANNOTATION = re.compile(
    r"[(\[]\s*(?:status|state|lifecycle|stand|statut|estado)\s*[:=]\s*"
    r"([A-Za-z_ ]{3,40})\s*[)\]]", re.IGNORECASE)


def status_annotation(text: str) -> str | None:
    """Read an explicit ``(status: X)`` annotation, if the renderer wrote one."""
    found = _STATUS_ANNOTATION.search(text or "")
    if not found:
        return None
    raw = "_".join(found.group(1).split()).upper()
    try:
        return normalize_state(raw)
    except ValueError:
        return None


# The evidence act that a derived state is at most licensed by, when the act
# is not supplied independently.
_STATE_DEFAULT_ACT: Mapping[str, str] = {
    "CONCEPTUAL": "ACTOR_INTENTION", "PROPOSED": "ACTOR_INTENTION",
    "DEPLOYMENT_PLANNED": "ACTOR_INTENTION", "PILOT_PLANNED": "ACTOR_INTENTION",
    "PROCUREMENT_PLANNED": "ACTOR_INTENTION", "BUDGET_REQUESTED": "ACTOR_INTENTION",
    "ANNOUNCED": "POLITICAL_COMMITMENT", "POLICY_ADOPTED": "FORMAL_POLICY_DECISION",
    "FUNDED": "BUDGET_AUTHORIZATION", "PROCUREMENT_OPEN": "PROCUREMENT_NOTICE",
    "CONTRACT_AWARDED": "CONTRACT_AWARD", "IN_DEVELOPMENT": "DEVELOPMENT_ACTIVITY",
    "TECHNICALLY_AVAILABLE": "TECHNICAL_DEMONSTRATION",
    "PILOT_ACTIVE": "PILOT_EVIDENCE", "PILOT_COMPLETED": "PILOT_EVIDENCE",
    "EXERCISED": "EXERCISE_EVIDENCE", "DEPLOYED_LIMITED": "DEPLOYMENT_EVIDENCE",
    "DEPLOYED": "DEPLOYMENT_EVIDENCE", "OPERATIONAL": "OPERATIONAL_USE_EVIDENCE",
    "SUSPENDED": "SUSPENSION_NOTICE", "CANCELLED": "CANCELLATION_NOTICE",
    "RETIRED": "RETIREMENT_NOTICE",
}

# Where a planning cue demotes a delivery state, this is what it demotes to.
_PLANNING_DEMOTION: Mapping[str, str] = {
    "OPERATIONAL": "DEPLOYMENT_PLANNED", "DEPLOYED": "DEPLOYMENT_PLANNED",
    "DEPLOYED_LIMITED": "DEPLOYMENT_PLANNED", "EXERCISED": "PILOT_PLANNED",
    "PILOT_COMPLETED": "PILOT_PLANNED", "PILOT_ACTIVE": "PILOT_PLANNED",
    "TECHNICALLY_AVAILABLE": "IN_DEVELOPMENT", "IN_DEVELOPMENT": "PROPOSED",
    "CONTRACT_AWARDED": "PROCUREMENT_PLANNED",
    "PROCUREMENT_OPEN": "PROCUREMENT_PLANNED", "FUNDED": "BUDGET_REQUESTED",
    "POLICY_ADOPTED": "PROPOSED",
}

_SUPPORTED_LANGUAGES = ("en", "de", "fr", "es", "it")
# Italian shares the Romance cue tables; a per-language table is only added
# where Italian diverges from both French and Spanish.
_IT_EXTRA: Mapping[str, tuple[str, ...]] = {
    "planning": (r"\bprevede\s+di\b", r"\bprevisto\b", r"\bdovrebbe\b",
                 r"\bsar[àa]\b", r"\bintende\b", r"\ba\s+partire\s+dal\s+20\d\d\b"),
}


def _language_keys(language: str) -> tuple[str, ...]:
    code = (language or "").strip().casefold()[:2]
    if code == "it":
        return ("fr", "es")
    if code in ("en", "de", "fr", "es"):
        return (code,)
    return ("en", "de", "fr", "es")


def _table_patterns(table: Mapping[str, tuple[str, ...]], language: str) -> tuple[str, ...]:
    out: list[str] = []
    for key in _language_keys(language):
        out.extend(table.get(key, ()))
    return tuple(out)


def _matches(text: str, patterns: Iterable[str]) -> str | None:
    for pattern in patterns:
        found = re.search(pattern, text, flags=re.IGNORECASE)
        if found:
            return found.group(0)
    return None


@dataclass(frozen=True)
class LifecycleReading(Record):
    """What one span says about the lifecycle state of its object."""

    reading_id: str
    state: str
    evidence_act: str
    planning_cue: str | None
    state_cue: str | None
    demoted_from: str | None
    rationale: str
    language: str

    def __post_init__(self) -> None:
        normalize_state(self.state)
        if self.evidence_act not in ACT_MAXIMUM_STATE:
            raise ValueError(f"unknown evidence act: {self.evidence_act}")
        if not act_licenses(self.evidence_act, self.state):
            raise ValueError(
                f"evidence act {self.evidence_act} does not license state {self.state}")


def derive_lifecycle(text: str, language: str = "en", *,
                     evidence_act: str | None = None) -> LifecycleReading:
    """Read lifecycle state and evidence act from a span.

    Futurity and modality dominate: when a planning cue and a delivery cue
    appear in the same span, the delivery state is demoted to its planning
    counterpart.  This is the rule that keeps "soll wieder in Betrieb gehen"
    at DEPLOYMENT_PLANNED and stops an intention from being recorded as
    operational use.
    """
    body = " ".join((text or "").split())
    planning_patterns = list(_table_patterns(_PLANNING_CUES, language))
    if (language or "").strip().casefold()[:2] == "it":
        planning_patterns.extend(_IT_EXTRA["planning"])
    planning_cue = _matches(body, planning_patterns)

    state_cue: str | None = None
    raw_state = "UNKNOWN"
    for candidate_state, table in _STATE_CUES:
        found = _matches(body, _table_patterns(table, language))
        if found:
            raw_state, state_cue = candidate_state, found
            break

    demoted_from: str | None = None
    state = raw_state
    if planning_cue and raw_state in _PLANNING_DEMOTION:
        demoted_from, state = raw_state, _PLANNING_DEMOTION[raw_state]
    elif planning_cue and raw_state == "UNKNOWN":
        # An intention needs a branch: "plans to deploy" is DEPLOYMENT_PLANNED,
        # not a bare proposal.  Fall back to PROPOSED only when the sentence
        # names no delivery activity at all.
        state = "PROPOSED"
        for planned_state, table in _PLANNED_OBJECT_CUES:
            found = _matches(body, _table_patterns(table, language))
            if found:
                state, state_cue = planned_state, found
                break

    if state == "UNKNOWN":
        act = evidence_act or "ACTOR_INTENTION"
        return LifecycleReading(
            stable_id("lifecycle-reading", body[:200], language, "UNKNOWN"),
            "UNKNOWN", act if act in ACT_MAXIMUM_STATE else "ACTOR_INTENTION",
            None, None, None,
            "no lifecycle cue in the span; state is not asserted", language or "und")

    act = (evidence_act or _STATE_DEFAULT_ACT[state]).strip().upper()
    if act not in ACT_MAXIMUM_STATE:
        raise ValueError(f"unknown evidence act: {evidence_act!r}")
    if not act_licenses(act, state):
        # The supplied act is weaker than the wording claims.  Evidence wins:
        # the state is lowered to the strongest state the act licenses.
        candidates = [s for s in licensed_states(act) if s != "UNKNOWN"]
        candidates.sort(key=lambda s: (len(PRESUPPOSED[s]), s), reverse=True)
        demoted_from = demoted_from or state
        state = candidates[0] if candidates else "UNKNOWN"

    if demoted_from:
        rationale = (f"delivery wording {demoted_from} demoted to {state}: "
                     f"the span carries the futurity cue {planning_cue!r}"
                     if planning_cue else
                     f"{demoted_from} lowered to {state}: evidence act {act} "
                     "licenses no stronger state")
    else:
        rationale = f"state {state} read from the cue {state_cue!r}"
    return LifecycleReading(
        stable_id("lifecycle-reading", body[:200], language, state, act),
        state, act, planning_cue, state_cue, demoted_from, rationale,
        language or "und")


# ---------------------------------------------------------------------------
# Section 8.4 — claim integration
# ---------------------------------------------------------------------------

SOURCE_AUTHORITIES = frozenset({
    "OFFICIAL_PRIMARY", "OFFICIAL_SECONDARY", "INSTITUTIONAL_PARTY",
    "VENDOR_SELF_DESCRIPTION", "INDEPENDENT_MEDIA", "PARTISAN_ADVOCACY",
    "UNKNOWN_AUTHORITY",
})

# Authorities whose bare assertion may not license delivery states without
# corroboration: a vendor saying its own system is operational is a vendor
# claim, not a demonstrated fact.
_SELF_INTERESTED = frozenset({"VENDOR_SELF_DESCRIPTION", "PARTISAN_ADVOCACY"})
_DELIVERY_STATES = frozenset({
    "TECHNICALLY_AVAILABLE", "PILOT_ACTIVE", "PILOT_COMPLETED", "EXERCISED",
    "DEPLOYED_LIMITED", "DEPLOYED", "OPERATIONAL",
})


@dataclass(frozen=True)
class LifecycleAssertion(Record):
    """A lifecycle claim with everything Section 8.4 requires it to carry."""

    assertion_id: str
    actor: str
    target_object: str
    state: str
    evidence_act: str
    valid_time: tuple[str | None, str | None]
    publication_time: str | None
    scope: tuple[str, ...]
    qualification: str | None
    source_authority: str
    disputed: bool
    superseded_by: str | None
    support_span_ids: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        normalize_state(self.state)
        if self.evidence_act not in ACT_MAXIMUM_STATE:
            raise ValueError(f"unknown evidence act: {self.evidence_act}")
        if self.source_authority not in SOURCE_AUTHORITIES:
            raise ValueError(f"unknown source authority: {self.source_authority}")
        if not self.actor.strip():
            raise ValueError("lifecycle assertion requires an explicit actor")
        if not self.target_object.strip():
            raise ValueError("lifecycle assertion requires a target object")
        if len(self.valid_time) != 2:
            raise ValueError("valid time must be a (start, end) pair")
        if self.state != "UNKNOWN" and not self.support_span_ids:
            raise ValueError("an asserted lifecycle state requires support spans")
        if not act_licenses(self.evidence_act, self.state):
            raise ValueError(
                f"evidence act {self.evidence_act} does not license state {self.state}; "
                "this is the plan-as-implementation defect the milestone forbids")
        if (self.source_authority in _SELF_INTERESTED
                and self.state in _DELIVERY_STATES and not self.qualification):
            raise ValueError(
                "a self-interested source asserting a delivery state requires an "
                "explicit qualification; an unqualified vendor claim may not be "
                "recorded as demonstrated fact")

    @property
    def permitted_entailments(self) -> tuple[str, ...]:
        if self.state == "UNKNOWN":
            return ()
        return tuple(sorted(PRESUPPOSED[normalize_state(self.state)] |
                            {normalize_state(self.state)}))

    @property
    def prohibited_stronger_formulations(self) -> tuple[str, ...]:
        if self.state == "UNKNOWN":
            return tuple(s for s in LIFECYCLE_STATES if s != "UNKNOWN")
        allowed = set(self.permitted_entailments)
        return tuple(s for s in LIFECYCLE_STATES
                     if s not in allowed and s != "UNKNOWN")


def lifecycle_assertion(*, actor: str, target_object: str, state: str,
                        evidence_act: str, support_span_ids: Iterable[str],
                        valid_time: tuple[str | None, str | None] = (None, None),
                        publication_time: str | None = None,
                        scope: Iterable[str] = (), qualification: str | None = None,
                        source_authority: str = "UNKNOWN_AUTHORITY",
                        disputed: bool = False,
                        superseded_by: str | None = None) -> LifecycleAssertion:
    spans = tuple(support_span_ids)
    normalized = normalize_state(state)
    return LifecycleAssertion(
        stable_id("lifecycle-assertion", actor, target_object, normalized,
                  evidence_act, "|".join(spans)),
        actor.strip(), target_object.strip(), normalized,
        evidence_act.strip().upper(), tuple(valid_time), publication_time,
        tuple(scope), qualification, source_authority, bool(disputed),
        superseded_by, spans, now_utc())


# ---------------------------------------------------------------------------
# Section 8.5 — report wording guard
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WordingVerdict(Record):
    verdict_id: str
    permitted: bool
    sentence_state: str
    claim_state: str
    violation: str | None
    detail: str


def check_report_wording(sentence: str, claim_state: str, *,
                         language: str = "en") -> WordingVerdict:
    """A sentence may not use a stronger lifecycle term than its claim."""
    supported = normalize_state(claim_state)
    annotated = status_annotation(sentence)
    if annotated is not None and not entails(supported, annotated):
        return WordingVerdict(
            stable_id("wording", sentence[:160], supported, annotated, "ANNOTATION"),
            False, annotated, supported, "LIFECYCLE_STRENGTHENED",
            f"the rendered status annotation asserts {annotated}; the supporting "
            f"claim only establishes {supported}, which does not entail it")
    reading = derive_lifecycle(sentence, language)
    sentence_state = reading.state
    if annotated is not None and stronger_than(annotated, sentence_state):
        sentence_state = annotated

    if sentence_state == "UNKNOWN":
        return WordingVerdict(
            stable_id("wording", sentence[:160], supported, "NO_LIFECYCLE_TERM"),
            True, "UNKNOWN", supported, None,
            "the sentence asserts no lifecycle state")
    if supported == "UNKNOWN":
        return WordingVerdict(
            stable_id("wording", sentence[:160], supported, sentence_state),
            False, sentence_state, supported, "LIFECYCLE_STATE_UNSUPPORTED",
            f"the sentence asserts {sentence_state} but the supporting claim "
            "records no lifecycle state at all")
    if entails(supported, sentence_state):
        return WordingVerdict(
            stable_id("wording", sentence[:160], supported, sentence_state),
            True, sentence_state, supported, None,
            f"the claim state {supported} entails the sentence state {sentence_state}")
    return WordingVerdict(
        stable_id("wording", sentence[:160], supported, sentence_state),
        False, sentence_state, supported, "LIFECYCLE_STRENGTHENED",
        f"the sentence asserts {sentence_state}; the supporting claim only "
        f"establishes {supported}, which does not entail it")


# Named critical-error categories the closure gate counts (Sections 14.3, 22).
CRITICAL_LIFECYCLE_ERRORS = (
    "plan_as_implementation",
    "announcement_as_existing_capability",
    "vendor_claim_as_demonstrated_fact",
    "pilot_as_operational",
    "exercise_as_deployment",
)

_PLANNING_STATES = frozenset({
    "CONCEPTUAL", "PROPOSED", "DEPLOYMENT_PLANNED", "PILOT_PLANNED",
    "PROCUREMENT_PLANNED", "BUDGET_REQUESTED",
})
_IMPLEMENTATION_STATES = frozenset({
    "IN_DEVELOPMENT", "TECHNICALLY_AVAILABLE", "DEPLOYED_LIMITED", "DEPLOYED",
    "OPERATIONAL", "CONTRACT_AWARDED",
})


def classify_lifecycle_error(claimed_state: str, supported_state: str, *,
                             source_authority: str = "UNKNOWN_AUTHORITY",
                             qualified: bool = False) -> str | None:
    """Name the critical error a state pair commits, or None when sound."""
    claimed = normalize_state(claimed_state)
    supported = normalize_state(supported_state)
    if entails(supported, claimed):
        if (source_authority in _SELF_INTERESTED and claimed in _DELIVERY_STATES
                and not qualified):
            return "vendor_claim_as_demonstrated_fact"
        return None
    if supported in _PLANNING_STATES and claimed in _IMPLEMENTATION_STATES:
        return "plan_as_implementation"
    if supported == "ANNOUNCED" and claimed in _DELIVERY_STATES:
        return "announcement_as_existing_capability"
    if supported in {"PILOT_PLANNED", "PILOT_ACTIVE", "PILOT_COMPLETED"} and claimed == "OPERATIONAL":
        return "pilot_as_operational"
    if supported == "EXERCISED" and claimed in {"DEPLOYED", "DEPLOYED_LIMITED", "OPERATIONAL"}:
        return "exercise_as_deployment"
    if source_authority in _SELF_INTERESTED and claimed in _DELIVERY_STATES and not qualified:
        return "vendor_claim_as_demonstrated_fact"
    return "lifecycle_state_strengthened"


# ---------------------------------------------------------------------------
# Section 8.6 — full-population migration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LifecycleCorrection(Record):
    """A versioned correction over a historical claim.

    Historical objects are never rewritten: the correction carries the
    original identifier and the original state alongside the repaired one.
    """

    correction_id: str
    origin_milestone: str
    subject_id: str
    original_modality: str | None
    original_state: str | None
    corrected_state: str
    corrected_evidence_act: str
    error_class: str | None
    rationale: str
    version: int
    recorded_time: str


# V5.1 recorded lifecycle-flavoured values in the flat MODALITIES field.  This
# maps each one to the state it was actually asserting, so historical objects
# can be re-read without being edited.
MODALITY_TO_STATE: Mapping[str, str] = {
    "PLANNED": "DEPLOYMENT_PLANNED", "PROPOSED": "PROPOSED", "INTENDED": "PROPOSED",
    "VENDOR_DESCRIBED": "UNKNOWN", "EXERCISED": "EXERCISED", "PILOTED": "PILOT_ACTIVE",
    "DEPLOYED": "DEPLOYED", "OPERATIONAL": "OPERATIONAL",
    "PREDICTED": "PROPOSED", "CONDITIONAL": "PROPOSED",
    "RECOMMENDED": "PROPOSED", "REQUIRED_BY_LAW": "POLICY_ADOPTED",
}


def migrate_claim(claim: Mapping[str, Any], *, origin_milestone: str,
                  version: int = 1) -> LifecycleCorrection | None:
    """Re-read one historical accepted claim under the lifecycle model.

    Returns a versioned correction when the recorded state was stronger than
    the evidence licenses, or ``None`` when the original reading survives.
    """
    subject_id = str(claim.get("claim_id") or claim.get("subject_id") or
                     claim.get("id") or "")
    if not subject_id:
        raise ValueError("historical claim requires an identifier")
    text = str(claim.get("normalized_statement") or claim.get("text") or
               claim.get("statement") or "")
    language = str(claim.get("language") or "en")
    recorded_modality = claim.get("modality")
    recorded_state = claim.get("lifecycle_state")

    reading = derive_lifecycle(text, language)
    claimed = None
    if recorded_state:
        claimed = normalize_state(str(recorded_state))
    elif recorded_modality and str(recorded_modality).upper() in MODALITY_TO_STATE:
        claimed = MODALITY_TO_STATE[str(recorded_modality).upper()]

    authority = str(claim.get("source_authority") or "UNKNOWN_AUTHORITY")
    if authority not in SOURCE_AUTHORITIES:
        authority = "UNKNOWN_AUTHORITY"
    qualified = bool(claim.get("qualifications") or claim.get("qualification"))

    if claimed is None or claimed == "UNKNOWN":
        if reading.state == "UNKNOWN":
            return None
        return LifecycleCorrection(
            stable_id("lifecycle-correction", origin_milestone, subject_id, str(version)),
            origin_milestone, subject_id,
            str(recorded_modality) if recorded_modality else None,
            claimed, reading.state, reading.evidence_act, None,
            "no lifecycle state was recorded; the span asserts "
            f"{reading.state} ({reading.rationale})", version, now_utc())

    error = classify_lifecycle_error(
        claimed, reading.state, source_authority=authority, qualified=qualified)
    if error is None:
        return None
    return LifecycleCorrection(
        stable_id("lifecycle-correction", origin_milestone, subject_id, str(version)),
        origin_milestone, subject_id,
        str(recorded_modality) if recorded_modality else None,
        claimed, reading.state, reading.evidence_act, error,
        f"recorded state {claimed} is not entailed by the span, which asserts "
        f"{reading.state}: {reading.rationale}", version, now_utc())


def migrate_population(claims: Iterable[Mapping[str, Any]], *,
                       origin_milestone: str) -> dict[str, Any]:
    """Run the lifecycle model over a whole historical population."""
    corrections: list[LifecycleCorrection] = []
    errors: dict[str, int] = {name: 0 for name in CRITICAL_LIFECYCLE_ERRORS}
    errors["lifecycle_state_strengthened"] = 0
    total = 0
    for claim in claims:
        total += 1
        correction = migrate_claim(claim, origin_milestone=origin_milestone)
        if correction is None:
            continue
        corrections.append(correction)
        if correction.error_class:
            errors[correction.error_class] = errors.get(correction.error_class, 0) + 1
    return {
        "origin_milestone": origin_milestone,
        "claims_examined": total,
        "corrections": [item.to_record() for item in corrections],
        "correction_count": len(corrections),
        "error_counts": errors,
        "historical_objects_rewritten_in_place": 0,
    }


def audit_population(assertions: Iterable[LifecycleAssertion | Mapping[str, Any]]
                     ) -> dict[str, int]:
    """Count residual critical lifecycle errors over accepted assertions.

    A LifecycleAssertion cannot be constructed with an unlicensed state, so a
    clean population necessarily scores zero; the audit exists to prove that
    over data that has already been admitted, including mapping-shaped rows
    that bypassed the constructor.
    """
    counts = {name: 0 for name in CRITICAL_LIFECYCLE_ERRORS}
    counts["lifecycle_state_strengthened"] = 0
    for item in assertions:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, default=None, obj=item: getattr(obj, key, default))
        state = get("state")
        act = get("evidence_act")
        if not state or not act:
            continue
        if act_licenses(str(act), str(state)):
            authority = str(get("source_authority") or "UNKNOWN_AUTHORITY")
            if (authority in _SELF_INTERESTED
                    and normalize_state(str(state)) in _DELIVERY_STATES
                    and not get("qualification")):
                counts["vendor_claim_as_demonstrated_fact"] += 1
            continue
        supported = ACT_MAXIMUM_STATE[str(act).strip().upper()]
        error = classify_lifecycle_error(
            str(state), supported,
            source_authority=str(get("source_authority") or "UNKNOWN_AUTHORITY"),
            qualified=bool(get("qualification")))
        if error:
            counts[error] = counts.get(error, 0) + 1
    return counts


def unlicensed_outcome(subject_id: str, state: str, evidence_act: str):
    """Capability-failure outcome for a state its evidence cannot license."""
    return capability_outcome(
        subject_kind="CLAIM_SUPPORT_RELATION", subject_id=subject_id,
        outcome="SYSTEM_CAPABILITY_FAILURE",
        rationale=(f"lifecycle state {state} is not licensed by evidence act "
                   f"{evidence_act}"),
        capability_failure_class="SEMANTIC_TYPE_ERROR")

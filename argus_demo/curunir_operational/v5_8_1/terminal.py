"""Pure V5.8.1 terminal and disposition state machine.

The selector reports what it found.  This module answers two later and
independent questions:

* what the selected semantic analysis actually binds; and
* what, if anything, may be done with that binding.

Every input is a typed fact already present in the selected analysis or its
declared structural context.  Confidence, unit identity, reference labels and
evaluation outcomes are deliberately not inputs.
"""

from __future__ import annotations

from dataclasses import dataclass


class TerminalContractViolation(ValueError):
    """A caller supplied a fact outside the frozen D39 vocabulary."""


PROPOSITION_STATUSES = frozenset({
    "PROPOSITION",
    "NO_SEMANTIC_PROPOSITION",
    "PROPOSITION_INDETERMINATE",
    "CONSTRUCTION_DEFECT",
})
ROLE_COMPLETENESS = frozenset({
    "BOUND_LOCAL",
    "BOUND_CONTEXT",
    "RECOVERABLE_BOUNDED",
    "UNRESOLVED",
    "ABSENT_BY_CONSTRUCTION",
})
BINDING_UNIQUENESS = frozenset({
    "UNIQUE", "MATERIAL_AMBIGUITY", "NONE",
})
REQUIRED_CONTEXT_STATUSES = frozenset({
    "NONE",
    "AVAILABLE_AND_UNIQUE",
    "AVAILABLE_AMBIGUOUS",
    "ABSENT",
    "NOT_TRANSPORTED",
})
REPAIRABILITIES = frozenset({
    "NO_REPAIR_OWED",
    "BOUNDED_REPAIR_PROVEN",
    "REPAIR_NAMED_NOT_PROVEN",
    "IRREPARABLE",
})
TERMINAL_STATES = frozenset({
    "ROLE_BINDING_ESTABLISHED",
    "ROLE_BINDING_RECOVERABLE",
    "ROLE_BINDING_PARTIAL",
    "ROLE_BINDING_UNRESOLVED",
    "ROLE_BINDING_INVALID",
})
DISPOSITIONS = frozenset({
    "EVIDENCE_BOUND",
    "RECOVERABLE_WITH_BOUNDARY_REPAIR",
    "QUARANTINED",
    "REJECTED",
})


@dataclass(frozen=True)
class TerminalFacts:
    """The exhaustive D39 axes needed by the frozen external ontology."""

    proposition_status: str
    subject_completeness: str
    predicate_completeness: str
    binding_uniqueness: str
    required_context_status: str
    repairability: str

    def validate(self) -> None:
        fields = (
            ("proposition_status", self.proposition_status,
             PROPOSITION_STATUSES),
            ("subject_completeness", self.subject_completeness,
             ROLE_COMPLETENESS),
            ("predicate_completeness", self.predicate_completeness,
             ROLE_COMPLETENESS),
            ("binding_uniqueness", self.binding_uniqueness,
             BINDING_UNIQUENESS),
            ("required_context_status", self.required_context_status,
             REQUIRED_CONTEXT_STATUSES),
            ("repairability", self.repairability, REPAIRABILITIES),
        )
        illegal = [
            f"{name}={value!r}" for name, value, allowed in fields
            if value not in allowed
        ]
        if illegal:
            raise TerminalContractViolation(
                "unknown D39 fact value: " + ", ".join(illegal)
            )


@dataclass(frozen=True)
class TerminalDecision:
    value: str
    rule: str


def derive_terminal(facts: TerminalFacts) -> TerminalDecision:
    """Apply the independently frozen five-state ontology."""

    facts.validate()
    if facts.proposition_status in {
        "NO_SEMANTIC_PROPOSITION", "CONSTRUCTION_DEFECT",
    }:
        return TerminalDecision(
            "ROLE_BINDING_INVALID", "NO_SEMANTIC_PROPOSITION",
        )

    subject = facts.subject_completeness
    predicate = facts.predicate_completeness
    resolved = {
        "BOUND_LOCAL", "BOUND_CONTEXT", "RECOVERABLE_BOUNDED",
        "ABSENT_BY_CONSTRUCTION",
    }
    unresolved_count = sum(
        value == "UNRESOLVED" for value in (subject, predicate)
    )
    resolved_count = sum(value in resolved for value in (subject, predicate))

    # A non-unique reading cannot become complete merely because one candidate
    # happens to carry spans.  It is PARTIAL only when exactly one required role
    # is defensibly bound; otherwise the role assignment is UNRESOLVED.
    if facts.binding_uniqueness != "UNIQUE":
        if unresolved_count == 1 and resolved_count == 1:
            return TerminalDecision(
                "ROLE_BINDING_PARTIAL",
                "ONE_ROLE_BOUND_OTHER_UNRESOLVED",
            )
        return TerminalDecision(
            "ROLE_BINDING_UNRESOLVED",
            "NON_UNIQUE_ROLE_ASSIGNMENT",
        )

    if (
        predicate in {"BOUND_LOCAL", "BOUND_CONTEXT"}
        and subject in {
            "BOUND_LOCAL", "BOUND_CONTEXT", "ABSENT_BY_CONSTRUCTION",
        }
    ):
        return TerminalDecision(
            "ROLE_BINDING_ESTABLISHED",
            "UNIQUE_COMPLETE_BINDING",
        )

    if (
        subject in resolved
        and predicate in resolved
        and "RECOVERABLE_BOUNDED" in {subject, predicate}
    ):
        return TerminalDecision(
            "ROLE_BINDING_RECOVERABLE",
            "UNIQUE_BOUNDED_CONTEXT_RECOVERY",
        )

    if unresolved_count == 1 and resolved_count == 1:
        return TerminalDecision(
            "ROLE_BINDING_PARTIAL",
            "ONE_ROLE_BOUND_OTHER_UNRESOLVED",
        )
    return TerminalDecision(
        "ROLE_BINDING_UNRESOLVED",
        "NO_DEFENSIBLE_COMPLETE_ROLE_ASSIGNMENT",
    )


def derive_disposition(
    facts: TerminalFacts,
    terminal: str,
) -> TerminalDecision:
    """Apply disposition precedence independently of the terminal crosswalk."""

    facts.validate()
    if terminal not in TERMINAL_STATES:
        raise TerminalContractViolation(
            f"unknown terminal state for disposition: {terminal!r}"
        )

    if facts.repairability == "BOUNDED_REPAIR_PROVEN":
        return TerminalDecision(
            "RECOVERABLE_WITH_BOUNDARY_REPAIR",
            "BOUNDED_REPAIR_PRECEDENCE",
        )
    if (
        terminal == "ROLE_BINDING_INVALID"
        and facts.repairability == "IRREPARABLE"
    ):
        return TerminalDecision(
            "REJECTED", "IRREPARABLE_NON_PROPOSITION",
        )
    if (
        terminal in {
            "ROLE_BINDING_UNRESOLVED", "ROLE_BINDING_PARTIAL",
        }
        or facts.binding_uniqueness != "UNIQUE"
        or facts.repairability != "NO_REPAIR_OWED"
        or facts.required_context_status != "NONE"
    ):
        return TerminalDecision(
            "QUARANTINED", "OUTSTANDING_SEMANTIC_OBLIGATION",
        )
    if terminal == "ROLE_BINDING_ESTABLISHED":
        return TerminalDecision(
            "EVIDENCE_BOUND", "COMPLETE_UNIQUE_UNOBLIGATED_BINDING",
        )
    # Keep the disposition adapter total over the valid terminal vocabulary.
    # A future terminal rule must not silently fall through to a publishable
    # disposition merely because it has no currently-known obligation.
    return TerminalDecision(
        "QUARANTINED", "NON_ESTABLISHED_TERMINAL",
    )


def validate_decisions(
    facts: TerminalFacts,
    terminal: TerminalDecision,
    disposition: TerminalDecision,
) -> None:
    """Refuse illegal output combinations even if a future caller drifts."""

    facts.validate()
    if terminal.value not in TERMINAL_STATES:
        raise TerminalContractViolation(
            f"unknown derived terminal: {terminal.value!r}"
        )
    if disposition.value not in DISPOSITIONS:
        raise TerminalContractViolation(
            f"unknown derived disposition: {disposition.value!r}"
        )
    if (
        disposition.value == "EVIDENCE_BOUND"
        and terminal.value != "ROLE_BINDING_ESTABLISHED"
    ):
        raise TerminalContractViolation(
            "only ROLE_BINDING_ESTABLISHED may be EVIDENCE_BOUND"
        )
    if (
        terminal.value == "ROLE_BINDING_INVALID"
        and disposition.value == "EVIDENCE_BOUND"
    ):
        raise TerminalContractViolation(
            "ROLE_BINDING_INVALID may not be EVIDENCE_BOUND"
        )

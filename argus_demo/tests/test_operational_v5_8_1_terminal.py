from __future__ import annotations

from dataclasses import replace

import pytest

from curunir_operational.v5_8_1 import terminal as T


pytestmark = pytest.mark.no_db


def facts(**changes) -> T.TerminalFacts:
    base = T.TerminalFacts(
        proposition_status="PROPOSITION",
        subject_completeness="BOUND_LOCAL",
        predicate_completeness="BOUND_LOCAL",
        binding_uniqueness="UNIQUE",
        required_context_status="NONE",
        repairability="NO_REPAIR_OWED",
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    ("value", "terminal"),
    [
        (facts(), "ROLE_BINDING_ESTABLISHED"),
        (
            facts(
                subject_completeness="ABSENT_BY_CONSTRUCTION",
            ),
            "ROLE_BINDING_ESTABLISHED",
        ),
        (
            facts(
                subject_completeness="RECOVERABLE_BOUNDED",
                required_context_status="AVAILABLE_AND_UNIQUE",
                repairability="BOUNDED_REPAIR_PROVEN",
            ),
            "ROLE_BINDING_RECOVERABLE",
        ),
        (
            facts(
                subject_completeness="UNRESOLVED",
                binding_uniqueness="NONE",
                required_context_status="ABSENT",
                repairability="REPAIR_NAMED_NOT_PROVEN",
            ),
            "ROLE_BINDING_PARTIAL",
        ),
        (
            facts(
                binding_uniqueness="MATERIAL_AMBIGUITY",
                repairability="IRREPARABLE",
            ),
            "ROLE_BINDING_UNRESOLVED",
        ),
        (
            facts(
                proposition_status="NO_SEMANTIC_PROPOSITION",
                subject_completeness="ABSENT_BY_CONSTRUCTION",
                predicate_completeness="ABSENT_BY_CONSTRUCTION",
                binding_uniqueness="NONE",
                repairability="IRREPARABLE",
            ),
            "ROLE_BINDING_INVALID",
        ),
    ],
)
def test_terminal_truth_table(value, terminal):
    assert T.derive_terminal(value).value == terminal


@pytest.mark.parametrize(
    ("value", "terminal", "disposition"),
    [
        (
            facts(
                subject_completeness="RECOVERABLE_BOUNDED",
                required_context_status="AVAILABLE_AND_UNIQUE",
                repairability="BOUNDED_REPAIR_PROVEN",
            ),
            "ROLE_BINDING_RECOVERABLE",
            "RECOVERABLE_WITH_BOUNDARY_REPAIR",
        ),
        (
            facts(
                proposition_status="NO_SEMANTIC_PROPOSITION",
                subject_completeness="ABSENT_BY_CONSTRUCTION",
                predicate_completeness="ABSENT_BY_CONSTRUCTION",
                binding_uniqueness="NONE",
                repairability="IRREPARABLE",
            ),
            "ROLE_BINDING_INVALID",
            "REJECTED",
        ),
        (
            facts(
                binding_uniqueness="MATERIAL_AMBIGUITY",
                repairability="IRREPARABLE",
            ),
            "ROLE_BINDING_UNRESOLVED",
            "QUARANTINED",
        ),
        (
            facts(
                subject_completeness="UNRESOLVED",
                binding_uniqueness="NONE",
                required_context_status="ABSENT",
                repairability="REPAIR_NAMED_NOT_PROVEN",
            ),
            "ROLE_BINDING_PARTIAL",
            "QUARANTINED",
        ),
        (facts(), "ROLE_BINDING_ESTABLISHED", "EVIDENCE_BOUND"),
    ],
)
def test_disposition_precedence(value, terminal, disposition):
    terminal_decision = T.derive_terminal(value)
    assert terminal_decision.value == terminal
    decision = T.derive_disposition(value, terminal)
    assert decision.value == disposition
    T.validate_decisions(value, terminal_decision, decision)


def test_unknown_fact_is_refused():
    value = facts(binding_uniqueness="FIRST_MATCH_WINS")
    with pytest.raises(T.TerminalContractViolation):
        T.derive_terminal(value)


def test_evidence_bound_requires_established_terminal():
    value = facts()
    with pytest.raises(T.TerminalContractViolation):
        T.validate_decisions(
            value,
            T.TerminalDecision("ROLE_BINDING_PARTIAL", "test"),
            T.TerminalDecision("EVIDENCE_BOUND", "test"),
        )


def test_non_established_terminal_without_obligations_is_quarantined():
    value = facts()
    terminal = T.TerminalDecision("ROLE_BINDING_RECOVERABLE", "synthetic")
    disposition = T.derive_disposition(value, terminal.value)
    assert disposition == T.TerminalDecision(
        "QUARANTINED", "NON_ESTABLISHED_TERMINAL",
    )
    T.validate_decisions(value, terminal, disposition)


def test_invalid_non_proposition_path_remains_rejected():
    value = facts(
        proposition_status="NO_SEMANTIC_PROPOSITION",
        subject_completeness="ABSENT_BY_CONSTRUCTION",
        predicate_completeness="ABSENT_BY_CONSTRUCTION",
        binding_uniqueness="NONE",
        repairability="IRREPARABLE",
    )
    terminal = T.derive_terminal(value)
    disposition = T.derive_disposition(value, terminal.value)
    assert terminal.value == "ROLE_BINDING_INVALID"
    assert disposition.value == "REJECTED"
    T.validate_decisions(value, terminal, disposition)

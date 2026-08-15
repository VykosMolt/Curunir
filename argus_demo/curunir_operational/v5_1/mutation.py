"""Required mutation battery for V5.1 (contract Section 30).

Executes the 28 required mutations.  Each mutation deliberately violates one
invariant of the composed V4/V5/V5.1 system and counts as CAUGHT only when the
real guard (imported from the frozen module that owns it, never a copy)
rejects the violation for the intended reason.  Any uncaught mutation makes
the whole battery INVALID — the summary fails closed.

Four guards have no better home and are owned by this module (their absence
was itself a Section 30 finding): the held-out isolation guard
(``read_sealed_answers``), the replay network guard
(``replay_network_guard``), the replay provider sentinel
(``ReplayProviderSentinel``), and the replay escape-path guard
(``resolve_custody_path``), plus the report immutability check
(``verify_report_immutable``).

General mechanisms only: every fixture is synthetic and fictional
(.example domains); nothing here encodes a campaign, source, or answer.
Research shadow only.
"""
from __future__ import annotations

import json
import socket
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from ..v4.io import write_json
from ..v4.kernel import ZeroWriteMonitor, ZeroWriteViolation, build_proposal, validate_proposal
from ..write_observation import measured_figures, observe_canonical_writes
from ..v4.models import (
    DerivativeMapping, ExtractionCandidate, NormalizedDocument,
    TranslationDerivative,
)
from .anti_memorization import build_term_manifest, scan_paths
from .claim_support import SemanticClaim, SupportAssessment
from .dependence import (
    DependenceAssessment, DependenceExplanation, PublicationRef,
    classify_dependence, corroboration_arithmetic, dependence_signal,
)
from .extraction import SemanticParse, admit_candidate, parse_semantics
from .freeze import freeze_production, verify_freeze
from .layout import analyze_layout
from .models import (
    NON_CORROBORATING_STATES, EvidenceRef, Record, capability_outcome,
    now_utc, sha256, stable_id,
)
from .packets import leakage_scan
from .report_planning import (
    atomic_proposition, material_omission_audit,
    qualification_preservation_check, validate_executive_summary,
)
from .source_identity import ContentEdge, IdentityAssessment, content_edge, role_edge

REQUIRED_MUTATIONS = 28

# Deterministic fixture timestamp (packets.PACKET_BUILD_TIME idiom): fixture
# records must not depend on the wall clock.
_FIXTURE_TIME = "2026-07-24T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Module-owned guards (no better home; see module docstring)
# ---------------------------------------------------------------------------

class HeldoutIsolationError(PermissionError):
    """Sealed held-out material was requested from an implementation context."""


class ReplayIsolationError(RuntimeError):
    """Replay attempted network, provider, or escape-path access."""


def read_sealed_answers(sealed_answers_path: str | Path,
                        completion_manifest_path: str | Path | None = None,
                        ) -> Mapping[str, Any]:
    """Held-out isolation guard: sealed answers open only after completion.

    A sealed-answers artifact (the shape ``packets.freeze_corpus`` writes,
    marked REVIEW_ENGINE_ONLY) may only be read when a completion manifest
    attests that the held-out evaluation finished AND binds the exact answer
    set by hash.  Implementation contexts without that manifest are refused.
    """
    payload = json.loads(Path(sealed_answers_path).read_text(encoding="utf-8"))
    marking = tuple((payload.get("access_marking") or {}).get("releasability") or ())
    if marking != ("REVIEW_ENGINE_ONLY",):
        raise HeldoutIsolationError(
            "artifact is not a sealed REVIEW_ENGINE_ONLY answer store")
    if completion_manifest_path is None:
        raise HeldoutIsolationError(
            "sealed answers may not be read from an implementation context: "
            "no held-out completion manifest was supplied")
    manifest_path = Path(completion_manifest_path)
    if not manifest_path.is_file():
        raise HeldoutIsolationError("held-out completion manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("heldout_evaluation_complete") is not True:
        raise HeldoutIsolationError("held-out evaluation is not attested complete")
    answers = dict(payload.get("answers") or {})
    if manifest.get("sealed_answers_hash") != sha256(answers):
        raise HeldoutIsolationError(
            "completion manifest does not attest this sealed answer set")
    return answers


@contextmanager
def replay_network_guard() -> Iterator[dict[str, int]]:
    """Replay network guard: any socket connect during replay is refused.

    The refusal is raised before any system call, so no packet ever leaves;
    the counter records refused attempts.  The original connect is always
    restored.
    """
    state = {"attempts": 0}
    original_connect = getattr(socket.socket, "connect")

    def refused(sock: socket.socket, address: Any) -> None:
        state["attempts"] += 1
        raise ReplayIsolationError(
            f"network connection refused during replay: {address!r}")

    setattr(socket.socket, "connect", refused)
    try:
        yield state
    finally:
        setattr(socket.socket, "connect", original_connect)


class ReplayProviderSentinel:
    """Replay provider sentinel: providers are never rerun during replay.

    Replay code receives this sentinel instead of a live provider interface;
    every invocation is a violation.  Persisted provider records are the only
    admissible replay input.
    """

    def __init__(self) -> None:
        self.attempts = 0

    def invoke(self, provider: str, request: Any = None) -> None:
        self.attempts += 1
        raise ReplayIsolationError(
            f"provider reinvocation refused during replay: {provider}")


def resolve_custody_path(replay_root: str | Path, recorded_path: str | Path) -> Path:
    """Replay escape-path guard: custody paths must stay inside the replay root.

    Historical manifests may carry repository-relative custody paths; replay
    must resolve them inside the supplied relocatable root and refuse any path
    that escapes it (relative traversal or absolute path outside the root).
    """
    root = Path(replay_root).resolve()
    recorded = Path(recorded_path)
    resolved = (recorded if recorded.is_absolute() else root / recorded).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ReplayIsolationError(
            f"custody path escapes the replay root: {recorded_path}")
    return resolved


def verify_report_immutable(report_path: str | Path, recorded_hash: str) -> dict[str, Any]:
    """Report immutability check: current bytes must match the recorded hash.

    Historical reports are append-only artifacts; corrections happen through
    versioned superseding reports, never in place.  Drift raises.
    """
    current = sha256(Path(report_path).read_bytes())
    if current != recorded_hash:
        raise ValueError(
            "historical report rewritten in place: recorded hash "
            f"{recorded_hash} does not match current {current}")
    return {"report_path": str(report_path), "recorded_hash": recorded_hash,
            "verdict": "PASS"}


# ---------------------------------------------------------------------------
# Synthetic fixtures (fictional, deterministic)
# ---------------------------------------------------------------------------

def _fixture_document(text: str, pages: tuple = (), language: str = "en") -> NormalizedDocument:
    source_hash = sha256(text.encode("utf-8"))
    derivative_hash = sha256(text)
    source_id = stable_id("source-object", source_hash)
    mapping = DerivativeMapping(
        stable_id("mapping", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "memory://fixture", 0, len(text), "EXACT_PAGE_CHARACTER")
    return NormalizedDocument(
        stable_id("document", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "pdftotext-layout", "mutation-fixture-1", language, text,
        (("BODY", 0, len(text)),), tuple(pages), (mapping,), (), (), _FIXTURE_TIME)


def _fixture_candidate(document: NormalizedDocument, start: int, end: int,
                       candidate_type: str) -> ExtractionCandidate:
    return ExtractionCandidate(
        stable_id("candidate", document.document_id, start, end, candidate_type),
        "case-fixture", document.document_id, document.source_object_id, start, end,
        "BODY", candidate_type, document.text[start:end], {}, "fixture-provider",
        "1.0", {"heuristic_uncalibrated": 1.0}, "EXACT_PAGE_CHARACTER", (),
        {"releasability": ["PUBLIC"]}, _FIXTURE_TIME, "UNREVIEWED")


def _paged_fixture() -> NormalizedDocument:
    pages, parts, cursor = [], [], 0
    for number in range(1, 5):
        page = ("TIDEWATER DISTRICT WEEKLY BULLETIN\n"
                "The district office published the quarterly figures for basin "
                f"{number}.\n"
                f"Page {number} of 4\n")
        parts.append(page)
        pages.append((number, cursor, cursor + len(page)))
        cursor += len(page)
    return _fixture_document("".join(parts), pages=tuple(pages))


def _resolved_outcome(subject_id: str):
    return capability_outcome(
        subject_kind="SOURCE_DEPENDENCE_RELATION", subject_id=subject_id,
        outcome="RESOLVED_CORRECTLY", rationale="mutation fixture outcome")


def _factual(text: str, *, certainty: str, evidence_state: str,
             contradiction_state: str = "NO_CONFLICT",
             qualifications: tuple[str, ...] = (),
             temporal_scope: tuple[str | None, str | None] = (None, None)):
    return atomic_proposition(
        text=text, kind="FACTUAL", modality="ASSERTED", certainty=certainty,
        evidence_state=evidence_state, contradiction_state=contradiction_state,
        qualifications=qualifications, temporal_scope=temporal_scope,
        supporting_claim_ids=("claim-1",), supporting_span_ids=("span-1",))


# ---------------------------------------------------------------------------
# The 28 mutation checks — each returns True only when the real guard caught
# the deliberate violation for the intended reason.
# ---------------------------------------------------------------------------

def _m01_semantically_wrong_span(workspace: Path) -> bool:
    document = _fixture_document(
        "the annual dredging schedule. The committee approved the works on 3 May 2027.")
    end = len("the annual dredging schedule")
    candidate = _fixture_candidate(document, 0, end, "CLAIM")
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, None, parse)
    return (result.stage == "REJECTED"
            and result.reclassification_suggestion == "ENTITY_MENTION")


def _m02_page_number_candidate(workspace: Path) -> bool:
    document = _paged_fixture()
    annotation = analyze_layout(document.text, document.pages,
                                "application/pdf", "pdftotext-layout")
    classes = {region_class for _s, _e, region_class in annotation.regions}
    start = document.text.index("Page 2 of 4")
    candidate = _fixture_candidate(document, start, start + len("Page 2 of 4"), "DATE")
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, annotation, parse)
    return ("PAGE_NUMBER" in classes and result.stage == "QUARANTINED"
            and result.candidate_function == "NAVIGATIONAL_METADATA")


def _m03_strip_polarity(workspace: Path) -> bool:
    try:
        SemanticClaim("claim-1", "the office published the register", "the office",
                      "published", "the register", "", "ASSERTED")
    except ValueError as error:
        return "polarity" in str(error)
    return False


def _m04_strip_modality(workspace: Path) -> bool:
    try:
        SemanticClaim("claim-1", "the office published the register", "the office",
                      "published", "the register", "POSITIVE", "")
    except ValueError as error:
        return "modality" in str(error)
    return False


def _m05_strip_temporal_scope(workspace: Path) -> bool:
    try:
        SemanticParse(
            stable_id("semantic-parse", "mutation-05"), "the committee", "approved",
            "the schedule", "the committee", "POSITIVE", "ASSERTED", (None, None),
            "EXPLICIT_DATE", (), None, (), "en", True, (), sha256("fixture span"))
    except ValueError as error:
        return "scope start" in str(error)
    return False


def _m06_merge_ambiguous_entities(workspace: Path) -> bool:
    try:
        IdentityAssessment(
            stable_id("identity-assessment", "mutation-06"), "entity-1",
            "ORGANIZATION", "entity-2", "ORGANIZATION", "SAME_ENTITY_SUPPORTED",
            (EvidenceRef("LEGAL_NAME", "src-1", None, "nearly identical legal names"),),
            "mutation forces a merge on name similarity alone", None, now_utc())
    except ValueError as error:
        return "name similarity" in str(error)
    return False


def _m07_conflate_host_publisher(workspace: Path) -> bool:
    hosting = (EvidenceRef("DOMAIN_OWNERSHIP_RECORD", "src-1", None,
                           "domain registration names the hosting company"),)
    try:
        role_edge(role="PUBLISHED_BY", subject_id="publication-1",
                  agent_entity_id="entity-host", evidence=hosting)
    except ValueError as error:
        control = role_edge(role="HOSTED_BY", subject_id="publication-1",
                            agent_entity_id="entity-host", evidence=hosting)
        return ("never supports PUBLISHED_BY" in str(error)
                and control.role == "HOSTED_BY")
    return False


def _m08_conflate_programme_vendor(workspace: Path) -> bool:
    try:
        IdentityAssessment(
            stable_id("identity-assessment", "mutation-08"), "entity-programme",
            "PROGRAMME", "entity-vendor", "VENDOR", "SAME_ENTITY_SUPPORTED",
            (EvidenceRef("DOMAIN", "src-1", None, "shared web domain"),
             EvidenceRef("PUBLICATION_METADATA", "src-1", None, "shared byline block")),
            "mutation conflates a programme with its vendor", None, now_utc())
    except ValueError as error:
        return "class-conflict" in str(error)
    return False


def _m09_reverse_origin_edge(workspace: Path) -> bool:
    timing = (EvidenceRef("PUBLICATION_TIMING", "publication-original", None,
                          "the claimed source appeared later"),)
    try:
        ContentEdge(
            stable_id("content-edge", "mutation-09"), "TRANSLATED_FROM",
            "publication-original", "publication-derived", timing, "UNSPECIFIED",
            (None, None), {}, None, now_utc())
        return False
    except ValueError as error:
        if "direction-licensing" not in str(error):
            return False
    downgraded = content_edge(relation="TRANSLATED_FROM",
                              source_id="publication-original",
                              target_id="publication-derived", evidence=timing)
    return (downgraded.relation == "UNKNOWN_DEPENDENCE"
            and downgraded.downgrade is not None
            and downgraded.downgrade.requested_relation == "TRANSLATED_FROM")


def _m10_translation_as_independent(workspace: Path) -> bool:
    text = "A fixture translation of the register notice."
    try:
        TranslationDerivative(
            stable_id("translation", "mutation-10"), "document-1", "de", "en",
            "fixture-provider", "1.0", text, "APPROXIMATE_SECTION", (),
            sha256(text), "AI_SECONDARY_REVIEW", independent_source=True)
    except ValueError as error:
        return "cannot be independent" in str(error)
    return False


def _m11_mirror_as_independent(workspace: Path) -> bool:
    fingerprint = sha256("identical manifestation bytes")
    left = PublicationRef("publication-a", "family-a", "left-venue.example", "en", fingerprint)
    right = PublicationRef("publication-b", "family-b", "right-venue.example", "en", fingerprint)
    mirror = classify_dependence(left, right, ())
    summary = corroboration_arithmetic(
        claim_family_id="claim-family-1", publications=(left, right),
        assessments=(mirror,))
    return (mirror.state == "MIRROR_MANIFESTATION"
            and mirror.state in NON_CORROBORATING_STATES
            and summary.independent_corroboration_count == 0)


def _m12_syndication_as_independent(workspace: Path) -> bool:
    wire = dependence_signal(
        kind="WIRE_SERVICE_INDICATION", strength="STRONG",
        detail="a wire-service banner appears on both manifestations",
        evidence_refs=(EvidenceRef("WIRE_SERVICE_INDICATION", "publication-e",
                                   None, "wire banner"),))
    explanation = DependenceExplanation(
        ("WIRE_SERVICE_INDICATION[STRONG]",), (), (), (), {}, True, False)
    try:
        DependenceAssessment(
            stable_id("dependence-assessment", "mutation-12"), "publication-e",
            "publication-f", "SYNDICATION_DERIVATIVE", (wire,), explanation,
            _resolved_outcome("publication-e|publication-f"), now_utc())
    except ValueError as error:
        return "affirmatively independent" in str(error)
    return False


def _m13_absence_upgraded_to_independence(workspace: Path) -> bool:
    explanation = DependenceExplanation((), (), (), (), {}, False, True)
    try:
        DependenceAssessment(
            stable_id("dependence-assessment", "mutation-13"), "publication-g",
            "publication-h", "INDEPENDENCE_SUPPORTED", (), explanation,
            _resolved_outcome("publication-g|publication-h"), now_utc())
    except ValueError as error:
        return "absence of discovered dependence" in str(error)
    return False


def _m14_unknown_as_corroboration(workspace: Path) -> bool:
    left = PublicationRef("publication-c", "family-c", "left-venue.example", "en")
    right = PublicationRef("publication-d", "family-d", "right-venue.example", "en")
    timing = dependence_signal(
        kind="PUBLICATION_TIMING", strength="MODERATE",
        detail="the second publication appeared hours after the first",
        evidence_refs=(EvidenceRef("PUBLICATION_TIMING", "publication-c", None,
                                   "captured publication timestamps"),))
    assessment = classify_dependence(left, right, (timing,))
    summary = corroboration_arithmetic(
        claim_family_id="claim-family-2", publications=(left, right),
        assessments=(assessment,))
    return (assessment.state == "INDEPENDENCE_UNKNOWN"
            and assessment.outcome.outcome == "EPISTEMICALLY_UNRESOLVABLE"
            and assessment.state in NON_CORROBORATING_STATES
            and summary.independent_corroboration_count == 0)


def _m15_omit_material_qualification(workspace: Path) -> bool:
    proposition = atomic_proposition(
        text="The expansion of the scheme is planned for next year",
        kind="FACTUAL", modality="PLANNED", certainty="QUALIFIED",
        evidence_state="QUALIFIED", qualifications=("PLANNED",),
        supporting_claim_ids=("claim-1",), supporting_span_ids=("span-1",))
    violations = qualification_preservation_check(
        proposition, "The scheme will be expanded next year.")
    clean = qualification_preservation_check(
        proposition, "The expansion of the scheme is planned for next year.")
    return (any(item.code == "MATERIAL_QUALIFICATION_OMITTED"
                and item.matched_text == "PLANNED" for item in violations)
            and clean == ())


def _m16_exec_summary_omits_counterevidence(workspace: Path) -> bool:
    body = _factual(
        "The registry entry was retracted by the issuing office",
        certainty="UNRESOLVED", evidence_state="RETRACTED",
        contradiction_state="RETRACTION",
        temporal_scope=("2027-01-01", "2027-06-30"))
    executive = _factual(
        "The registry entry remains current",
        certainty="UNRESOLVED", evidence_state="UNKNOWN")
    violations = validate_executive_summary((executive,), (body,))
    return any(item.code == "EXEC_OMITS_DECISIVE_COUNTEREVIDENCE"
               for item in violations)


def _m17_inference_as_fact(workspace: Path) -> bool:
    try:
        SupportAssessment(
            stable_id("support-assessment", "mutation-17"), "claim-1", "INFERENCE",
            "INFERENCE_ONLY", ("evidence-1",), (), (), False, (), (),
            (("evidence-1", "INFERENCE_ONLY"),), True,
            "mutation forces an inference-only relation into accepted reporting",
            now_utc())
    except ValueError as error:
        return "accepted for reporting" in str(error)
    return False


def _m18_omit_correction(workspace: Path) -> bool:
    proposition = _factual(
        "The office issued a correction to the earlier figure",
        certainty="QUALIFIED", evidence_state="QUALIFIED",
        contradiction_state="CORRECTION", qualifications=("CORRECTED",))
    audit = material_omission_audit((proposition,), (), {})
    silent = (audit.dispositions[proposition.proposition_id] == "SYSTEM_CAPABILITY_FAILURE"
              and len(audit.capability_failures) == 1
              and audit.capability_failures[0].capability_failure_class
              == "PROPOSITION_RENDER_FAILURE")
    control = material_omission_audit((proposition,), (proposition.proposition_id,), {})
    return silent and control.dispositions[proposition.proposition_id] == \
        "PUBLISHED_WITH_QUALIFICATION"


def _m19_expose_heldout_label(workspace: Path) -> bool:
    leaked = {"blinded_material": {
        "note": "Prior reviewers found this one CANNOT_ADJUDICATE."}}
    findings = leakage_scan(leaked, "CANNOT_ADJUDICATE")
    menu = {"blinded_material": {"reviewer_instructions": (
        "Permitted decisions: CORRECT, INCORRECT, PARTIALLY_CORRECT, AMBIGUOUS, "
        "CANNOT_ADJUDICATE, and EPISTEMICALLY_UNRESOLVABLE. Choose "
        "CANNOT_ADJUDICATE when you cannot reach a verdict for any other reason.")}}
    return (any(item["kind"] == "ANSWER_VERBATIM" for item in findings)
            and leakage_scan(menu, "CANNOT_ADJUDICATE") == ())


def _m20_modify_after_freeze(workspace: Path) -> bool:
    policy = workspace / "evaluation_policy.py"
    policy.write_text("SUPPORT_THRESHOLD = 0.5\n", encoding="utf-8")
    manifest_path = workspace / "freeze_manifest.json"
    freeze_production(
        scope_paths={"PRODUCTION_CODE": (policy,)},
        thresholds={"support_threshold": 0.5},
        prompts={"review": "Judge only the supplied packet."},
        packet_builder_version="v5.1-mutation-fixture",
        provider_versions={"baseline": "fixture-1"}, output_path=manifest_path)
    before = verify_freeze(manifest_path)
    policy.write_text("SUPPORT_THRESHOLD = 0.4\n", encoding="utf-8")
    after = verify_freeze(manifest_path)
    return (before["verdict"] == "PASS" and after["verdict"] == "INVALID"
            and after["reason"] == "POST_FREEZE_MODIFICATION_DETECTED"
            and len(after["drifted_files"]) == 1)


def _m21_campaign_phrase_in_production(workspace: Path) -> bool:
    campaign_root = workspace / "campaign"
    campaign_root.mkdir()
    (campaign_root / "source_object_manifest.json").write_text(json.dumps([{
        "title": "Tidewater Continuity Register",
        "requested_urls": ["https://registry.example/continuity-register"],
        "final_urls": ["https://registry.example/continuity-register"],
    }]), encoding="utf-8")
    manifest = build_term_manifest({"fixture": campaign_root})
    encoded = ('def classify(title):\n'
               '    if title == "Tidewater Continuity Register":\n'
               '        return "PROGRAMME_EXISTENCE_CLAIM"\n'
               '    return "UNKNOWN"\n')
    production = workspace / "pkg" / "semantic_rules.py"
    production.parent.mkdir()
    production.write_text(encoded, encoding="utf-8")
    fixture = workspace / "tests" / "test_fixture_rules.py"
    fixture.parent.mkdir()
    fixture.write_text(encoded, encoding="utf-8")
    report = scan_paths((production, fixture), manifest)
    production_hits = [item for item in report["occurrences"]
                       if item["path"] == str(production)]
    fixture_hits = [item for item in report["occurrences"]
                    if item["path"] == str(fixture)]
    return (report["verdict"] == "BLOCKED_SUSPICIOUS_CASE_ENCODING"
            and production_hits
            and all(item["classification"] == "SUSPICIOUS_CASE_ENCODING"
                    for item in production_hits)
            and fixture_hits
            and all(item["classification"] == "TEST_FIXTURE_ONLY"
                    for item in fixture_hits))


def _m22_read_sealed_answers(workspace: Path) -> bool:
    answers = {"packet-fixture": "NO_DEPENDENCE_FOUND"}
    sealed = workspace / "sealed_answers.json"
    sealed.write_text(json.dumps({
        "access_marking": {"releasability": ["REVIEW_ENGINE_ONLY"]},
        "answers": answers}), encoding="utf-8")
    try:
        read_sealed_answers(sealed)
        return False
    except HeldoutIsolationError as error:
        if "implementation context" not in str(error):
            return False
    manifest = workspace / "completion_manifest.json"
    manifest.write_text(json.dumps({
        "heldout_evaluation_complete": True,
        "sealed_answers_hash": sha256(answers)}), encoding="utf-8")
    return read_sealed_answers(sealed, manifest) == answers


def _m23_proposal_survives_invalidation(workspace: Path) -> bool:
    proposal = build_proposal(
        case_id="case-fixture", proposed_concept="captured-observation",
        target_table_or_action="records", proposed_field_values={"field": "value"},
        source_object_ids=("src-1",), claim_ids=("claim-1",),
        evidence_basis_ids=("basis-1",),
        source_independence_state="NO_DEPENDENCE_FOUND",
        identity_state="SAME_ENTITY_ACCEPTED", contradiction_state="NONE",
        correction_retraction_state="CURRENT", mapping_precision="EXACT_CHARACTER",
        dependencies=("dependency-1",), provider="fixture-provider",
        creator_actor_id="creator-1", access_marking={"releasability": ["PUBLIC"]})
    inspection = {"tables": {"records": ["field"]}, "action_signatures": {}}
    common = dict(validator_node_id="KERNEL_REVIEW_NODE",
                  reviewer_actor_id="reviewer-1", known_claim_ids={"claim-1"},
                  known_basis_ids={"basis-1"}, known_source_ids={"src-1"})
    mutated = validate_proposal(proposal, inspection, known_dependencies=set(), **common)
    control = validate_proposal(proposal, inspection,
                                known_dependencies={"dependency-1"}, **common)
    return (mutated.resulting_status == "APPROVAL_BLOCKED"
            and not mutated.dependencies_valid
            and any("dependency missing" in reason for reason in mutated.rationale)
            and control.resulting_status == "HUMAN_REVIEW_REQUIRED")


def _m24_canonical_write_attempt(workspace: Path) -> bool:
    root = workspace / "kernelroot"
    (root / "argus").mkdir(parents=True)
    (root / "schema.sql").write_bytes(b"-- fixture schema\n")
    (root / "argus" / "actions.py").write_bytes(b"# fixture actions\n")
    monitor = ZeroWriteMonitor(root)
    connect_refused = command_refused = False
    try:
        monitor.refuse_connect(("db.internal.example", 5432))
    except ZeroWriteViolation:
        connect_refused = True
    try:
        monitor.refuse_command(["psql", "--command", "select 1"])
    except ZeroWriteViolation:
        command_refused = True
    try:
        monitor.refuse_connect(("db.internal.example", 8080))
        monitor.refuse_command(["echo", "shadow-only"])
    except ZeroWriteViolation:
        return False
    return connect_refused and command_refused and monitor.attempts == 2


def _m25_network_during_replay(workspace: Path) -> bool:
    original_connect = getattr(socket.socket, "connect")
    with replay_network_guard() as state:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(("203.0.113.10", 80))
            refused = False
        except ReplayIsolationError:
            refused = True
        finally:
            sock.close()
    return (refused and state["attempts"] == 1
            and getattr(socket.socket, "connect") is original_connect)


def _m26_provider_reinvocation(workspace: Path) -> bool:
    sentinel = ReplayProviderSentinel()
    try:
        sentinel.invoke("live-extraction-provider", {"query": "anything"})
        return False
    except ReplayIsolationError:
        return sentinel.attempts == 1


def _m27_escape_custody_path(workspace: Path) -> bool:
    root = workspace / "replayroot"
    (root / "content").mkdir(parents=True)
    inside = resolve_custody_path(root, "content/sha256-object")
    escapes = 0
    for attempt in ("../outside-object", str(workspace / "absolute-escape")):
        try:
            resolve_custody_path(root, attempt)
        except ReplayIsolationError:
            escapes += 1
    return inside.is_relative_to(root.resolve()) and escapes == 2


def _m28_rewrite_report_in_place(workspace: Path) -> bool:
    report_path = workspace / "historical_report.md"
    report_path.write_text("# Findings\nEvery statement carries mapped evidence.\n",
                           encoding="utf-8")
    recorded = sha256(report_path.read_bytes())
    before = verify_report_immutable(report_path, recorded)["verdict"] == "PASS"
    report_path.write_text("# Findings\nOne statement was quietly strengthened.\n",
                           encoding="utf-8")
    try:
        verify_report_immutable(report_path, recorded)
        return False
    except ValueError as error:
        return before and "rewritten in place" in str(error)


# ---------------------------------------------------------------------------
# Battery specification and runner
# ---------------------------------------------------------------------------

_MUTATIONS: tuple[tuple[str, str, str, Callable[[Path], bool]], ...] = (
    ("accept a semantically wrong span: a noun-phrase span typed as CLAIM",
     "the admission gate rejects non-propositional claim spans",
     "extraction.admit_candidate: non-propositional CLAIM span REJECTED "
     "with ENTITY_MENTION reclassification", _m01_semantically_wrong_span),
    ("promote a page number to an analytical extraction candidate",
     "layout quarantine keeps page furniture out of analysis",
     "layout.analyze_layout + extraction.admit_candidate: PAGE_NUMBER region "
     "QUARANTINED as NAVIGATIONAL_METADATA", _m02_page_number_candidate),
    ("strip polarity from a claim record",
     "polarity is mandatory; the constructor fails loudly",
     "claim_support.SemanticClaim.__post_init__: uninterpretable polarity "
     "raises ValueError", _m03_strip_polarity),
    ("strip modality from a claim record",
     "modality is mandatory; the constructor fails loudly",
     "claim_support.SemanticClaim.__post_init__: uninterpretable modality "
     "raises ValueError", _m04_strip_modality),
    ("strip the temporal scope from a parse that states a temporal basis",
     "a stated temporal basis cannot exist without its scope",
     "extraction.SemanticParse.__post_init__: stated temporal basis requires "
     "a scope start", _m05_strip_temporal_scope),
    ("merge two ambiguous entities on name similarity alone",
     "name similarity alone never merges entities",
     "source_identity.IdentityAssessment.__post_init__: name-similarity-only "
     "merge refused", _m06_merge_ambiguous_entities),
    ("conflate the hosting agent with the publisher from domain evidence alone",
     "hosting evidence licenses HOSTED_BY, never PUBLISHED_BY",
     "source_identity.role_edge: domain hosting evidence alone never supports "
     "PUBLISHED_BY", _m07_conflate_host_publisher),
    ("conflate a programme with its vendor without official-identifier evidence",
     "class-conflict pairs require official-identifier or explicit-statement "
     "evidence to merge",
     "source_identity.IdentityAssessment.__post_init__: class-conflict pair "
     "merge refused", _m08_conflate_programme_vendor),
    ("assert a reversed directional origin edge without direction-licensing evidence",
     "directional relations require direction-licensing evidence or are downgraded",
     "source_identity.ContentEdge direction-licensing guard; content_edge "
     "downgrades to UNKNOWN_DEPENDENCE with an EdgeDowngrade record",
     _m09_reverse_origin_edge),
    ("count a translation as an independent source",
     "a translation derivative can never be independent evidence",
     "v4.models.TranslationDerivative.__post_init__: independent_source "
     "translation refused", _m10_translation_as_independent),
    ("count a mirror manifestation as independent corroboration",
     "a mirror is the same manifestation and never corroborates",
     "dependence.classify_dependence MIRROR_MANIFESTATION is non-corroborating; "
     "corroboration_arithmetic counts zero", _m11_mirror_as_independent),
    ("declare a syndication derivative affirmatively independent",
     "a derivative pair cannot be affirmatively independent",
     "dependence.DependenceAssessment.__post_init__: derivative state cannot "
     "carry affirmative independence", _m12_syndication_as_independent),
    ("upgrade NO_DEPENDENCE_FOUND (absence only) to INDEPENDENCE_SUPPORTED",
     "absence of discovered dependence is never confirmed independence",
     "dependence.DependenceAssessment.__post_init__: absence-only support "
     "cannot construct INDEPENDENCE_SUPPORTED", _m13_absence_upgraded_to_independence),
    ("count an unknown-dependence pair as independent corroboration",
     "unknown dependence adds zero corroboration",
     "dependence.classify_dependence INDEPENDENCE_UNKNOWN is non-corroborating; "
     "corroboration_arithmetic counts zero", _m14_unknown_as_corroboration),
    ("omit a material qualification from the rendered sentence",
     "every material qualification must be lexically realized",
     "report_planning.qualification_preservation_check: "
     "MATERIAL_QUALIFICATION_OMITTED violation", _m15_omit_material_qualification),
    ("omit decisive counterevidence from the executive summary",
     "the executive summary must carry decisive counterevidence states",
     "report_planning.validate_executive_summary: "
     "EXEC_OMITS_DECISIVE_COUNTEREVIDENCE violation",
     _m16_exec_summary_omits_counterevidence),
    ("publish an inference-only relation as accepted fact",
     "inference-only support can never be accepted for reporting",
     "claim_support.SupportAssessment.__post_init__: INFERENCE_ONLY can never "
     "be accepted for reporting", _m17_inference_as_fact),
    ("silently omit a correction-bearing proposition from the report plan",
     "a silent omission is a system capability failure, never a gap",
     "report_planning.material_omission_audit: missing disposition becomes "
     "SYSTEM_CAPABILITY_FAILURE (PROPOSITION_RENDER_FAILURE)", _m18_omit_correction),
    ("expose the sealed held-out label inside reviewer-visible text",
     "reviewer-visible payloads never carry the sealed answer",
     "packets.leakage_scan: ANSWER_VERBATIM finding on review-history prose; "
     "instruction menus remain exempt", _m19_expose_heldout_label),
    ("modify evaluation logic after the production freeze",
     "any post-freeze drift invalidates the run",
     "freeze.verify_freeze: POST_FREEZE_MODIFICATION_DETECTED with the drifted "
     "file recorded", _m20_modify_after_freeze),
    ("encode a campaign-specific phrase in production code",
     "production code must stay campaign-agnostic",
     "anti_memorization.scan_paths: SUSPICIOUS_CASE_ENCODING blocks the "
     "production occurrence while test fixtures classify TEST_FIXTURE_ONLY",
     _m21_campaign_phrase_in_production),
    ("read sealed held-out answers from an implementation context",
     "sealed answers open only after an attested completion manifest",
     "mutation.read_sealed_answers: HeldoutIsolationError without a completion "
     "manifest; attested manifest opens the store", _m22_read_sealed_answers),
    ("keep a kernel proposal valid after its dependency was invalidated",
     "proposal lifecycle fails closed on dependency invalidation",
     "v4.kernel.validate_proposal: invalidated dependency yields "
     "APPROVAL_BLOCKED with 'dependency missing'", _m23_proposal_survives_invalidation),
    ("attempt a canonical database write during a shadow run",
     "canonical connections and write commands are refused",
     "v4.kernel.ZeroWriteMonitor: ZeroWriteViolation on canonical port connect "
     "and write subprocess; benign operations pass", _m24_canonical_write_attempt),
    ("open a network connection during replay",
     "replay is fully offline; every connect is refused",
     "mutation.replay_network_guard: ReplayIsolationError before any packet "
     "leaves; original connect restored", _m25_network_during_replay),
    ("reinvoke a provider during replay",
     "persisted provider records are the only replay input",
     "mutation.ReplayProviderSentinel: ReplayIsolationError on provider "
     "invocation during replay", _m26_provider_reinvocation),
    ("resolve a repository-relative custody path that escapes the replay root",
     "custody paths must resolve inside the relocatable replay root",
     "mutation.resolve_custody_path: ReplayIsolationError on relative and "
     "absolute escape paths; inside paths resolve", _m27_escape_custody_path),
    ("rewrite a historical report artifact in place",
     "historical reports are immutable; corrections are versioned",
     "mutation.verify_report_immutable: ValueError on in-place rewrite against "
     "the recorded hash", _m28_rewrite_report_in_place),
)

if len(_MUTATIONS) != REQUIRED_MUTATIONS:
    raise RuntimeError("the required mutation battery must hold exactly 28 mutations")


@dataclass(frozen=True)
class MutationRecord(Record):
    """One executed mutation and whether the composed system caught it."""

    mutation_id: int
    description: str
    expected_failure_reason: str
    caught: bool
    catch_mechanism: str

    def __post_init__(self) -> None:
        if not 1 <= self.mutation_id <= REQUIRED_MUTATIONS:
            raise ValueError(f"mutation id outside 1..{REQUIRED_MUTATIONS}: {self.mutation_id}")
        for name in ("description", "expected_failure_reason", "catch_mechanism"):
            if not getattr(self, name).strip():
                raise ValueError(f"mutation record requires a non-empty {name}")
        if not isinstance(self.caught, bool):
            raise ValueError("caught must be a boolean finding")


def run_required_mutations(output_path: str | Path) -> dict[str, Any]:
    """Execute all 28 Section 30 mutations against the real guards.

    Every mutation runs in its own throwaway workspace (tmp dirs only; no
    repository state is touched).  A mutation whose check crashes counts as
    uncaught.  Any uncaught mutation makes the verdict INVALID — fail closed.
    The written report is deterministic modulo ``generated_time``.
    """
    records: list[dict[str, Any]] = []
    battery_writes = observe_canonical_writes(label="v5_1.run_required_mutations")
    with battery_writes as observed, tempfile.TemporaryDirectory(
            prefix="curunir-v5-1-mutations-") as tmp:
        base = Path(tmp)
        for index, (description, expected, mechanism, check) in enumerate(_MUTATIONS, 1):
            workspace = base / f"mutation-{index:02d}"
            workspace.mkdir()
            try:
                caught = bool(check(workspace))
                recorded_mechanism = mechanism
            except Exception as error:  # a crashing check is an uncaught mutation
                caught = False
                recorded_mechanism = f"UNEXPECTED_{type(error).__name__}"
            records.append(MutationRecord(
                index, description, expected, caught, recorded_mechanism).to_record())
    uncaught = [record["mutation_id"] for record in records if not record["caught"]]
    report: dict[str, Any] = {
        "milestone": "CURUNIR_EPISTEMIC_CAPABILITY_CLOSURE_AND_CLEAN_GENERALIZATION_V5_1",
        "contract_section": 30,
        "required_mutations": REQUIRED_MUTATIONS,
        "mutations": records,
        "caught": REQUIRED_MUTATIONS - len(uncaught),
        "uncaught": uncaught,
        **measured_figures(observed),
        "verdict": "PASS" if not uncaught else "INVALID",
    }
    report["content_hash"] = sha256(report)
    report["generated_time"] = now_utc()
    write_json(Path(output_path), report)
    return report


def mutations(**spec: Any) -> dict[str, Any]:
    """CLI wrapper (name fixed by cli.FORWARDED_COMMANDS)."""
    return run_required_mutations(spec["output_path"])

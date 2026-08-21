"""Historical V6.7 information-flow exploits at the production boundary."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_analytic.themes import create_theme, refresh_theme
from curunir_fabric.contracts import ManifestationRecord
from curunir_operational.missions import MissionWorkflow
from curunir_operational.projection import Projection
from curunir_workbench.projections import MissionProjection

from operational_support import (BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT,
                                 RESTRICTED_MARKING, make_store, t)
from semantic_support import T0
from workbench_support import CTX_A, CTX_B, RESTRICTED_MARK, make_workbench

pytestmark = pytest.mark.no_db

SECRET_TITLE = "COVERT-OP-BLUEJAY-SEALED"
LEI = "SEC0000000000000010"


def _gleif(status: str) -> bytes:
    return (
        '{"data":{"id":"%s","attributes":{"lei":"%s","entity":'
        '{"legalName":{"name":"Restricted Entity"},"jurisdiction":"NO",'
        '"status":"%s","otherNames":[],"legalAddress":{"city":"Oslo",'
        '"country":"NO"}},"registration":{"status":"ISSUED",'
        '"initialRegistrationDate":"2014-03-02",'
        '"lastUpdateDate":"2026-08-01"}}}}' % (LEI, LEI, status)
    ).encode()


def _plant_restricted(pipeline, status: str, retrieval_time: str, prior: str | None = None) -> str:
    body = _gleif(status)
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native = f"lei/{LEI}"
    manifestation_id = digest_id("manifestation", native, digest)
    record = ManifestationRecord(
        manifestation_id=manifestation_id,
        source_id="gleif",
        connector_id="gleif-test",
        connector_version="1.0",
        native_id=native,
        request_url=native,
        final_url=native,
        content_sha256=digest,
        content_store_path=str(path),
        media_type="application/json",
        temporal_status="LIVE",
        source_time=None,
        archive_capture_time=None,
        retrieval_time=retrieval_time,
        http_status=200,
        redirects=(),
        etag="",
        last_modified="",
        truncated=False,
        retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native, retrieval_time),
        prior_manifestation_id=prior,
        marking=RESTRICTED_MARK,
    )
    pipeline.store.append(
        "FABRIC_MANIFESTATION_RECORDED",
        record,
        recorded_time=pipeline.now_fn(),
        actor="fixture",
    )
    return manifestation_id


def test_authoring_and_background_refresh_cannot_declassify_restricted_basis(tmp_path):
    pipeline, public_context = make_workbench(tmp_path)
    first = _plant_restricted(pipeline, "ACTIVE", T0)
    pipeline.process_new_evidence()
    claim = next(
        candidate for candidate in public_context.store.current_claims().values()
        if candidate["subject_ref"] == f"LEI:{LEI}"
        and candidate["predicate"] == "entity_status"
    )
    assert claim["marking"]["compartments"] == ["SPECIAL"]

    # Historical exploit 1: PUBLIC authoring over a SPECIAL claim returned and
    # persisted a PUBLIC theme.
    theme = create_theme(
        public_context,
        title=SECRET_TITLE,
        description="restricted tasking rationale",
        supporting_claim_ids=[claim["claim_id"]],
    )
    assert theme["marking"]["compartments"] == ["SPECIAL"]

    # Historical exploit 2: a later PUBLIC background refresh reclassified the
    # SPECIAL theme and exposed its analyst-authored prose.
    _plant_restricted(
        pipeline,
        "INACTIVE",
        "2026-08-17T15:00:00+00:00",
        prior=first,
    )
    pipeline.process_new_evidence()
    refreshed = refresh_theme(
        public_context,
        theme["theme_id"],
        caused_by="background-propagation",
    )
    assert refreshed["marking"]["compartments"] == ["SPECIAL"]
    assert public_context.store.current_themes()[theme["theme_id"]]["marking"] \
        ["compartments"] == ["SPECIAL"]

    cleared = MissionProjection(public_context.store, CTX_A)
    uncleared = MissionProjection(public_context.store, CTX_B)
    assert cleared.get("analytic_theme", theme["theme_id"]) is not None
    assert uncleared.get("analytic_theme", theme["theme_id"]) is None
    assert not uncleared.transitions(theme["theme_id"])
    assert SECRET_TITLE not in json.dumps(uncleared.overview())


def test_hidden_workflow_transition_cannot_leak_or_change_lower_status(tmp_path):
    store = make_store(tmp_path)
    workflow = MissionWorkflow(store)
    requirement = workflow.open_requirement(
        mission_context="projection-test",
        question="What did the restricted assessment conclude?",
        affected_ids=(),
        priority="HIGH",
        rationale="public tasking",
        required_evidence_type="ASSESSMENT",
        owning_role="ANALYST",
        closure_criteria="reviewed evidence exists",
        due_time=None,
        recorded_time=t(0),
        marking=BASE_MARKING,
        actor="analyst",
    )
    workflow.transition(
        "requirement",
        requirement["requirement_id"],
        "ANSWERED",
        actor_id="cleared-analyst",
        actor_kind="HUMAN",
        evidence_refs=("restricted-evidence",),
        note="SEALED-ANSWER",
        recorded_time=t(1),
        marking=RESTRICTED_MARKING,
    )

    lower = Projection(store).view(LOW_CONTEXT)["information_requirements"][0]
    assert lower["status"] == "OPEN"
    assert lower["transitions"] == []
    assert "SEALED-ANSWER" not in json.dumps(lower)

    cleared = Projection(store).view(HIGH_CONTEXT)["information_requirements"][0]
    assert cleared["status"] == "ANSWERED"
    assert cleared["transitions"][0]["note"] == "SEALED-ANSWER"

"""Starter catalog: a small set of real public sources with genuinely
different acquisition shapes, enough to prove the generic architecture.

    wikidata      — multilingual entity lookup API (search + expand)
    gleif         — legal-entity registry API with page pagination
    sec-edgar     — regulatory filing full-text search + archive fetch (deep history)
    wayback       — web archive: historical enumeration + capture fetch
    live-web      — ordinary public web page retrieval (current only)
    federal-register-feed — official gazette RSS change feed (append-only)

Descriptors use the ARGUS Source Intelligence vocabulary; profiles carry the
fabric acquisition-shape facts. Nothing here is mocked: every entry binds to
a working connector against the real public endpoint.
"""
from __future__ import annotations

from argus.source_intelligence.models import SourceDescriptor, digest_id

from .contracts import CapabilityProfile
from .registry import register_source
from .store import FabricStore

CATALOG_VERSION = "fabric-starter-catalog-1"


def _descriptor(now: str, **overrides) -> SourceDescriptor:
    base = dict(
        source_subtype="", access_methods=("HTTP_GET",), authentication_requirement="NONE",
        known_identifiers=(), update_cadence="UNKNOWN", archive_availability="UNKNOWN",
        official_status="UNOFFICIAL", authority_candidate="NOT_ASSESSED",
        independence_notes="", terms_metadata="", robots_metadata="", rate_metadata="",
        review_state="REGISTERED_STARTER_CATALOG", valid_time=(None, None),
        transaction_time=now, created_at=now,
    )
    base.update(overrides)
    return SourceDescriptor(**base)


def _profile(now: str, source_id: str, connector_id: str, connector_version: str, **overrides) -> CapabilityProfile:
    base = dict(
        profile_id=digest_id("profile", source_id, CATALOG_VERSION),
        source_id=source_id, connector_id=connector_id, connector_version=connector_version,
        time_coverage=(None, None), historical_depth="UNKNOWN", update_latency="UNKNOWN",
        pagination="NONE", edit_behaviour="UNKNOWN", cost_class="FREE",
        rate_note="", authorization="USER_AGENT_ONLY", native_id_scheme="",
        available_fields=(), known_biases=(), known_gaps=(), archive_compatible=True,
        created_time=now,
    )
    base.update(overrides)
    return CapabilityProfile(**base)


def starter_catalog(now: str) -> list[tuple[SourceDescriptor, CapabilityProfile]]:
    return [
        (
            _descriptor(
                now, source_id="wikidata", canonical_name="Wikidata",
                publisher_name="Wikimedia Foundation", source_type="PUBLIC_DATASET",
                base_urls=("https://www.wikidata.org",), jurisdictions=("GLOBAL",),
                languages=("mul",), coverage_domains=("entities", "organizations", "identifiers"),
                access_class="PUBLIC_API",
                capabilities=("ENTITY_LOOKUP", "STRUCTURED_METADATA", "RELATIONSHIP_LOOKUP"),
                update_cadence="CONTINUOUS",
                independence_notes="community-edited tertiary source; treat as pointer, not authority",
            ),
            _profile(
                now, "wikidata", "wikidata-v1", "1.0",
                supported_operations=("SEARCH", "LOOKUP"), historical_depth="VERSIONED",
                update_latency="REALTIME", pagination="CURSOR", edit_behaviour="EDITS_VISIBLE",
                native_id_scheme="WIKIDATA_QID",
                available_fields=("labels", "aliases", "descriptions", "external_identifiers", "claims"),
                known_biases=("coverage skews toward well-documented entities",),
                known_gaps=("no guarantee of completeness for any jurisdiction",),
            ),
        ),
        (
            _descriptor(
                now, source_id="gleif", canonical_name="GLEIF LEI Records",
                publisher_name="Global Legal Entity Identifier Foundation",
                source_type="CORPORATE_REGISTRY",
                base_urls=("https://api.gleif.org",), jurisdictions=("GLOBAL",),
                languages=("en",), coverage_domains=("legal_entities", "corporate_identity"),
                access_class="PUBLIC_REGISTRY", official_status="OFFICIAL",
                capabilities=("ENTITY_LOOKUP", "STRUCTURED_METADATA", "CORPORATE_OWNERSHIP"),
                update_cadence="DAILY",
            ),
            _profile(
                now, "gleif", "gleif-lei-v1", "1.0",
                supported_operations=("SEARCH", "LOOKUP"), historical_depth="CURRENT_ONLY",
                update_latency="DAILY", pagination="PAGE_NUMBER", edit_behaviour="EDITS_VISIBLE",
                native_id_scheme="LEI",
                available_fields=("legal_name", "other_names", "jurisdiction", "status",
                                  "addresses", "registration_dates", "successor"),
                known_gaps=("only entities that obtained an LEI; historical officers not provided",),
            ),
        ),
        (
            _descriptor(
                now, source_id="sec-edgar", canonical_name="SEC EDGAR Full-Text Search",
                publisher_name="U.S. Securities and Exchange Commission",
                source_type="COMPANY_FILINGS",
                base_urls=("https://efts.sec.gov", "https://www.sec.gov"),
                jurisdictions=("US",), languages=("en",),
                coverage_domains=("regulatory_filings", "corporate_disclosure", "officers_directors"),
                access_class="PUBLIC_API", official_status="OFFICIAL",
                capabilities=("FILING_LOOKUP", "FULL_TEXT", "HISTORICAL_ARCHIVE", "STRUCTURED_METADATA"),
                update_cadence="CONTINUOUS",
                rate_metadata="SEC fair-access guidance: max 10 requests/second, declared UA",
            ),
            _profile(
                now, "sec-edgar", "sec-edgar-fts-v1", "1.0",
                supported_operations=("SEARCH", "FETCH"), historical_depth="DEEP_ARCHIVE",
                time_coverage=("2001-01-01T00:00:00+00:00", None),
                update_latency="HOURLY", pagination="CURSOR", edit_behaviour="APPEND_ONLY",
                native_id_scheme="SEC_ACCESSION",
                available_fields=("accession", "form", "file_date", "ciks", "display_names", "document"),
                rate_note="<=10 req/s, identifying User-Agent required",
                known_gaps=("full-text search covers filings from 2001 onward",),
            ),
        ),
        (
            _descriptor(
                now, source_id="wayback", canonical_name="Internet Archive Wayback Machine",
                publisher_name="Internet Archive", source_type="PUBLIC_ARCHIVE",
                base_urls=("https://web.archive.org",), jurisdictions=("GLOBAL",),
                languages=("mul",), coverage_domains=("web_history",),
                access_class="PUBLIC_ARCHIVE",
                capabilities=("HISTORICAL_ARCHIVE", "SOURCE_VERSIONING", "DOCUMENT_DISCOVERY"),
                update_cadence="CONTINUOUS", archive_availability="SELF",
                independence_notes="derivative preservation of third-party content; origin remains the archived publisher",
            ),
            _profile(
                now, "wayback", "wayback-machine-v1", "1.0",
                supported_operations=("HISTORICAL_ENUMERATE", "HISTORICAL_FETCH"),
                historical_depth="DEEP_ARCHIVE", time_coverage=("1996-01-01T00:00:00+00:00", None),
                update_latency="IRREGULAR", pagination="CURSOR", edit_behaviour="APPEND_ONLY",
                native_id_scheme="WAYBACK_CAPTURE",
                available_fields=("timestamp", "original_url", "digest", "mimetype", "statuscode"),
                known_biases=("capture density follows crawl priorities, not importance",),
                known_gaps=("robots-excluded and uncrawled pages are absent",),
                archive_compatible=False,  # it IS the archive; re-archiving is circular
            ),
        ),
        (
            _descriptor(
                now, source_id="live-web", canonical_name="Ordinary Public Web",
                publisher_name="(per-site)", source_type="OTHER_PUBLIC_SOURCE",
                base_urls=("https://",), jurisdictions=("GLOBAL",), languages=("mul",),
                coverage_domains=("current_web_content",),
                access_class="PUBLIC_ORDINARY_WEB",
                capabilities=("FULL_TEXT", "OFFICIAL_STATEMENTS"),
                update_cadence="CONTINUOUS",
            ),
            _profile(
                now, "live-web", "web-page-v1", "1.0",
                supported_operations=("FETCH", "POLL"), historical_depth="CURRENT_ONLY",
                update_latency="REALTIME", edit_behaviour="EDITS_SILENT",
                native_id_scheme="URL",
                known_gaps=("no discovery of its own; needs URLs from planning or pivots",),
            ),
        ),
        (
            _descriptor(
                now, source_id="federal-register-feed",
                canonical_name="US Federal Register — current documents feed",
                publisher_name="U.S. Office of the Federal Register",
                source_type="OFFICIAL_GAZETTE",
                base_urls=("https://www.federalregister.gov/api/v1/documents.rss",),
                jurisdictions=("US",), languages=("en",),
                coverage_domains=("official_gazette", "rulemaking", "executive_documents"),
                access_class="PUBLIC_FEED", official_status="OFFICIAL",
                capabilities=("CHANGE_FEED", "OFFICIAL_STATEMENTS", "LEGAL_TEXT"),
                update_cadence="DAILY",
            ),
            _profile(
                now, "federal-register-feed", "rss-feed-v1", "1.0",
                supported_operations=("FETCH", "POLL"), historical_depth="CURRENT_ONLY",
                update_latency="DAILY", edit_behaviour="APPEND_ONLY",
                native_id_scheme="FEED_GUID",
                available_fields=("title", "link", "guid", "published"),
                known_gaps=("feed window only; older documents require the API/site",),
            ),
        ),
    ]


def seed_starter_catalog(store: FabricStore, *, recorded_time: str, actor: str) -> list[str]:
    registered = []
    for descriptor, profile in starter_catalog(recorded_time):
        register_source(store, descriptor, profile, recorded_time=recorded_time, actor=actor)
        registered.append(descriptor.source_id)
    return registered

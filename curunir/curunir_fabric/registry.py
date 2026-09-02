"""The source registry: what each registered source can provide.

Identity comes from the kernel SourceDescriptor, capabilities from the fabric
CapabilityProfile, and health from status events. All three are replayed from
the store.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from argus.source_intelligence.models import SourceDescriptor, digest_id
from argus.source_intelligence.registry import SourceRegistry

from .contracts import CapabilityProfile, SourceStatusEvent, UPDATE_LATENCIES
from .store import FabricStore

_LATENCY_RANK = {name: rank for rank, name in enumerate(UPDATE_LATENCIES)}


def _descriptor_record(descriptor: SourceDescriptor) -> dict:
    data = {field: getattr(descriptor, field) for field in descriptor.__dataclass_fields__}
    data["record_type"] = "fabric_source_descriptor"
    return data


def descriptor_from_record(record: dict) -> SourceDescriptor:
    data = {k: v for k, v in record.items() if k != "record_type"}
    for field in ("base_urls", "jurisdictions", "languages", "coverage_domains", "access_methods",
                  "known_identifiers", "capabilities"):
        data[field] = tuple(data[field])
    data["valid_time"] = tuple(data["valid_time"])
    return SourceDescriptor(**data)


def register_source(store: FabricStore, descriptor: SourceDescriptor, profile: CapabilityProfile,
                    *, recorded_time: str, actor: str) -> None:
    if profile.source_id != descriptor.source_id:
        raise ValueError("profile does not belong to this source descriptor")
    # The kernel registry validates the descriptor before anything is persisted.
    load_registry(store).registry.register(descriptor)
    store.append("FABRIC_SOURCE_DESCRIBED", _descriptor_record(descriptor),
                 recorded_time=recorded_time, actor=actor)
    store.append("FABRIC_SOURCE_PROFILE_RECORDED", profile, recorded_time=recorded_time, actor=actor)


def record_source_status(store: FabricStore, *, source_id: str, connector_id: str, kind: str,
                         operation: str, detail: str, observed_time: str, actor: str) -> SourceStatusEvent:
    status = SourceStatusEvent(
        status_id=digest_id("src-status", source_id, operation, kind, observed_time),
        source_id=source_id, connector_id=connector_id, kind=kind,
        operation=operation, detail=detail, observed_time=observed_time,
    )
    store.append("FABRIC_SOURCE_STATUS_RECORDED", status, recorded_time=observed_time, actor=actor)
    return status


@dataclass(frozen=True)
class RegistryView:
    """Registry state replayed from the store, with capability queries."""
    registry: SourceRegistry
    profiles: dict[str, dict]        # source_id -> latest profile record
    statuses: dict[str, list[dict]]  # source_id -> status records in order

    def current_descriptors(self) -> list[SourceDescriptor]:
        latest: dict[str, SourceDescriptor] = {}
        for entry in self.registry.entries:
            known = latest.get(entry.source_id)
            if known is None or entry.transaction_time > known.transaction_time:
                latest[entry.source_id] = entry
        return [latest[key] for key in sorted(latest)]

    def descriptor(self, source_id: str) -> SourceDescriptor | None:
        versions = self.registry.versions(source_id)
        return max(versions, key=lambda item: item.transaction_time, default=None)

    def profile(self, source_id: str) -> dict | None:
        return self.profiles.get(source_id)

    def families(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for descriptor in self.current_descriptors():
            grouped.setdefault(descriptor.source_type, []).append(descriptor.source_id)
        return grouped

    def capable_sources(self, *, operation: str | None = None, capability: str | None = None,
                        language: str | None = None, jurisdiction: str | None = None,
                        historical: bool | None = None) -> list[SourceDescriptor]:
        """Sources whose descriptor and profile match the filters."""
        matches = []
        for descriptor in self.current_descriptors():
            profile = self.profiles.get(descriptor.source_id)
            if operation is not None:
                if profile is None or operation not in profile["supported_operations"]:
                    continue
            if capability is not None and capability not in descriptor.capabilities:
                continue
            if language is not None and descriptor.languages and language not in descriptor.languages \
                    and "mul" not in descriptor.languages:
                continue
            if jurisdiction is not None and descriptor.jurisdictions \
                    and jurisdiction not in descriptor.jurisdictions and "GLOBAL" not in descriptor.jurisdictions:
                continue
            if historical is True:
                deep = profile is not None and (
                    profile["historical_depth"] in ("VERSIONED", "DEEP_ARCHIVE")
                    or any(op.startswith("HISTORICAL_") for op in profile["supported_operations"]))
                if not deep:
                    continue
            matches.append(descriptor)
        return matches

    def monitorable_sources(self, *, max_latency: str) -> list[SourceDescriptor]:
        """Sources that can be polled and update at least as fast as ``max_latency``."""
        ceiling = _LATENCY_RANK[max_latency]
        found = []
        for descriptor in self.current_descriptors():
            profile = self.profiles.get(descriptor.source_id)
            if profile is None:
                continue
            operations = set(profile["supported_operations"])
            if not operations & {"POLL", "FETCH", "SEARCH"}:
                continue
            if _LATENCY_RANK.get(profile["update_latency"], len(UPDATE_LATENCIES)) <= ceiling:
                found.append(descriptor)
        return found

    def last_status(self, source_id: str, kind: str) -> dict | None:
        for record in reversed(self.statuses.get(source_id, [])):
            if record["kind"] == kind:
                return record
        return None


def load_registry(store: FabricStore) -> RegistryView:
    registry = SourceRegistry()
    for record in store.records_of("fabric_source_descriptor"):
        registry = registry.register(descriptor_from_record(record))
    profiles = store.latest_by_id("fabric_source_profile", "source_id")
    statuses: dict[str, list[dict]] = {}
    for record in store.records_of("fabric_source_status"):
        statuses.setdefault(record["source_id"], []).append(record)
    return RegistryView(registry=registry, profiles=profiles, statuses=statuses)

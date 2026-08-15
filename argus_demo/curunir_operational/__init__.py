"""Curunír operational plane: mission-data fabric and operational workbench.

Research-shadow subsystem. Boundary: SYSTEM_OF_ENGAGEMENT_AND_ANALYSIS — it
ingests, normalizes, associates, visualizes, explains, alerts, recommends,
records analyst actions, exports and replays. It is not a system of record and
never claims authoritative control of external records. It owns no canonical
PostgreSQL path and performs no network access.
"""
from __future__ import annotations

PACKAGE_VERSION = "curunir-operational-v1"
ENGAGEMENT_BOUNDARY = "SYSTEM_OF_ENGAGEMENT_AND_ANALYSIS"

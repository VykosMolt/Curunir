"""Curunír operational plane: mission-data fabric and operational workbench.

It ingests, normalizes, associates, visualizes, explains, alerts, recommends,
records analyst actions, exports and replays. It is not a system of record,
claims no authority over external records, makes no network access, and owns
no canonical PostgreSQL path (tests/test_operational_protection.py enforces this).
"""
from __future__ import annotations

PACKAGE_VERSION = "curunir-operational-v1"
ENGAGEMENT_BOUNDARY = "SYSTEM_OF_ENGAGEMENT_AND_ANALYSIS"

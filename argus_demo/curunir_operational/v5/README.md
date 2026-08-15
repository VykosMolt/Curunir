# Curunír V5

V5 is an additive research-shadow layer for frozen epistemic review and CLI-driven longitudinal updates.
It does not claim human accuracy, run a scheduler, approve kernel proposals, write PostgreSQL, or modify the
frozen canonical schema/actions.

```bash
PYTHONPATH=argus_demo argus_demo/.venv/bin/python -m curunir_operational.v5.cli stage1 \
  --v4-root argus_demo/artifacts/curunir_public_source_intelligence_and_kernel_admission_v4_20260722 \
  --artifact-root argus_demo/artifacts/curunir_epistemic_validation_and_longitudinal_operations_v5_20260722

PYTHONPATH=argus_demo argus_demo/.venv/bin/python -m curunir_operational.v5.cli stage2 \
  --v4-root argus_demo/artifacts/curunir_public_source_intelligence_and_kernel_admission_v4_20260722 \
  --artifact-root argus_demo/artifacts/curunir_epistemic_validation_and_longitudinal_operations_v5_20260722
```

Packet-level review profiles are deterministic isolated equivalents. Independent Sol subagent audits, when
available, are recorded separately as `MODEL_PANEL_SECONDARY_REVIEW`; neither mechanism is human review.

"""Deterministic append-only and canonical JSON persistence helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .models import canonical_json


def write_json(path: str | Path, value: Any, *, refuse_existing: bool = False) -> Path:
    target = Path(path)
    if refuse_existing and target.exists():
        raise FileExistsError(f"immutable output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def append_jsonl(path: str | Path, records: Iterable[Any]) -> int:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(canonical_json(record) + "\n"); count += 1
        handle.flush()
    return count


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path: str | Path) -> list[Any]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]

"""The executable repository ledger: measure what the suite collects and record it.

`tests/ledger.json` records, per test module, the number of tests it collected
when last accepted, plus the product modules that are entry points and the
paths that were excised. `tests/test_ledger.py` checks the working tree
against it. Update the ledger deliberately, after review:

    python tools/ledger.py --update
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = PACKAGE_ROOT / "tests" / "ledger.json"
PRODUCT_PACKAGES = sorted(p.name for p in PACKAGE_ROOT.glob("curunir_*") if p.is_dir())
# Modules that are run rather than imported.
ENTRY_POINT_NAMES = {"__init__", "__main__", "cli", "server"}


def collected_tests() -> dict[str, int]:
    """Tests collected per module, from pytest itself."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=PACKAGE_ROOT, capture_output=True, text=True)
    counts: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        if "::" in line:
            counts[line.split("::", 1)[0]] += 1
    if not counts:
        raise RuntimeError("pytest collected nothing:\n" + result.stdout + result.stderr)
    return dict(sorted(counts.items()))


def product_modules() -> dict[str, Path]:
    modules = {}
    for package in PRODUCT_PACKAGES:
        for path in sorted((PACKAGE_ROOT / package).rglob("*.py")):
            if "__pycache__" not in path.parts:
                modules[".".join(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)] = path
    return modules


def _imported_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    package_parts = path.relative_to(PACKAGE_ROOT).with_suffix("").parts[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(package_parts[:len(package_parts) - node.level + 1]
                                + ((node.module,) if node.module else ()))
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def importers() -> dict[str, set[str]]:
    """Which files import each product module."""
    modules = product_modules()
    users: dict[str, set[str]] = {name: set() for name in modules}
    scanned = [p for p in PACKAGE_ROOT.rglob("*.py")
               if ".venv" not in p.parts and "__pycache__" not in p.parts
               and not p.relative_to(PACKAGE_ROOT).parts[0] in ("research", "missions", "docs")]
    for path in scanned:
        own = ".".join(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
        for name in _imported_names(path):
            if name in users and name != own:
                users[name].add(path.relative_to(PACKAGE_ROOT).as_posix())
    return users


def is_entry_point(name: str, path: Path) -> bool:
    if name.rsplit(".", 1)[-1] in ENTRY_POINT_NAMES:
        return True
    return 'if __name__ == "__main__":' in path.read_text(encoding="utf-8")


def module_level_skips(path: Path) -> list[str]:
    """Unconditional skips at module level, which hide a whole module from the suite."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = ast.unparse(node.value.func)
            if call in ("pytest.skip", "skip"):
                found.append(ast.unparse(node.value))
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            text = ast.unparse(node.value)
            if "mark.skip(" in text or text.endswith("mark.skip"):
                found.append(text)
    return found


def build() -> dict:
    current = json.loads(LEDGER_PATH.read_text(encoding="utf-8")) if LEDGER_PATH.exists() else {}
    modules = product_modules()
    return {
        "format": "curunir-executable-ledger-v1",
        "how_to_update": "python tools/ledger.py --update, after review of the change that moved the numbers",
        "tests_collected": collected_tests(),
        "entry_points": sorted(name for name, path in modules.items() if is_entry_point(name, path)),
        "excised": current.get("excised", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="rewrite tests/ledger.json from the working tree")
    args = parser.parse_args()
    ledger = build()
    if args.update:
        LEDGER_PATH.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {LEDGER_PATH}")
    else:
        print(json.dumps(ledger, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

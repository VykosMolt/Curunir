"""Pack, unpack and verify the pinned ARGUS kernel tarball.

The kernel tree is identified by one hash (CURUNIR_V6_7_RECONSTRUCTION.json).
CI downloads the tarball from a GitHub release and unpacks it with this tool,
which refuses any tree whose hash differs from the pinned one.

    python tools/kernel_bundle.py verify ../kernel/argus
    python tools/kernel_bundle.py pack ../kernel/argus --out ../kernel/argus_kernel_pinned_4c173df7.tar.gz
    python tools/kernel_bundle.py unpack ../kernel/argus_kernel_pinned_4c173df7.tar.gz --into ../kernel
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconstruct_v67 import MANIFEST_PATH, ReconstructionError, kernel_identity  # noqa: E402


def pinned_hash() -> str:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return manifest["external_kernel"]["tree_sha256"]


def verify(tree: Path) -> str:
    """The tree's hash, if it is the pinned one."""
    digest, count, _ = kernel_identity(tree)
    expected = pinned_hash()
    if digest != expected:
        raise ReconstructionError(f"kernel tree hash {digest} is not the pinned {expected}")
    print(json.dumps({"kernel": str(tree), "files": count, "sha256": digest}))
    return digest


def pack(tree: Path, out: Path) -> None:
    verify(tree)
    _, _, sources = kernel_identity(tree)
    with tarfile.open(out, "w:gz") as archive:
        for path in sources:
            archive.add(path, arcname=f"argus/{path.relative_to(tree).as_posix()}", recursive=False)
    print(json.dumps({"packed": str(out), "files": len(sources)}))


def unpack(bundle: Path, into: Path) -> None:
    target = into / "argus"
    if target.exists():
        raise ReconstructionError(f"{target} already exists; remove it first")
    with tarfile.open(bundle, "r:gz") as archive:
        for member in archive.getmembers():
            inside = member.name == "argus" or member.name.startswith("argus/")
            if not inside or ".." in member.name.split("/") or not (member.isfile() or member.isdir()):
                raise ReconstructionError(f"unexpected tarball member: {member.name}")
        archive.extractall(into, filter="data")
    verify(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify").add_argument("tree", type=Path)
    packer = commands.add_parser("pack")
    packer.add_argument("tree", type=Path)
    packer.add_argument("--out", type=Path, required=True)
    unpacker = commands.add_parser("unpack")
    unpacker.add_argument("bundle", type=Path)
    unpacker.add_argument("--into", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify(args.tree)
        elif args.command == "pack":
            pack(args.tree, args.out)
        else:
            unpack(args.bundle, args.into)
    except ReconstructionError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

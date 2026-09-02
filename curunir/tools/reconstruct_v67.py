"""Build and smoke-test V6.7 from committed state plus the pinned kernel."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = Path(
    subprocess.run(
        ["git", "-C", str(PACKAGE_ROOT), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
)
MANIFEST_PATH = PACKAGE_ROOT / "CURUNIR_V6_7_RECONSTRUCTION.json"


class ReconstructionError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def kernel_identity(root: Path) -> tuple[str, int, list[Path]]:
    if not root.is_dir() or root.is_symlink():
        raise ReconstructionError(f"kernel is not a real directory: {root}")
    sources: list[Path] = []
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(name for name in directories if name != "__pycache__")
        for name in directories:
            if (current_path / name).is_symlink():
                raise ReconstructionError("kernel contains a symlinked directory")
        for name in sorted(files):
            path = current_path / name
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.suffix != ".py":
                raise ReconstructionError(f"kernel contains an unpinned non-source file: {path}")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ReconstructionError(f"kernel source is not a single-link regular file: {path}")
            sources.append(path)
    sources.sort(key=lambda path: path.relative_to(root).as_posix())
    digest = hashlib.sha256()
    for path in sources:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), len(sources), sources


def _copy_kernel(source: Path, destination: Path, files: list[Path]) -> None:
    destination.mkdir(mode=0o755)
    for path in files:
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def reconstruct(*, kernel: Path, output: Path, ref: str) -> dict[str, object]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected_kernel = manifest["external_kernel"]
    actual_hash, actual_count, sources = kernel_identity(kernel)
    if actual_hash != expected_kernel["tree_sha256"] or actual_count != expected_kernel["python_file_count"]:
        raise ReconstructionError(
            "kernel identity mismatch: "
            f"expected {expected_kernel['tree_sha256']} ({expected_kernel['python_file_count']} files), "
            f"got {actual_hash} ({actual_count} files)"
        )
    expected_requirements = manifest["runtime"]["requirements_sha256"]
    if _sha256(PACKAGE_ROOT / "requirements.txt") != expected_requirements:
        raise ReconstructionError("working requirements.txt does not match the reconstruction manifest")
    if output.exists() or output.is_symlink():
        raise ReconstructionError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    resolved_commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", f"{ref}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage.", dir=output.parent))
    archive_handle = tempfile.NamedTemporaryFile(
        prefix="curunir-v67-", suffix=".tar", dir=output.parent, delete=False
    )
    archive = Path(archive_handle.name)
    archive_handle.close()
    try:
        subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", "-o", str(archive), ref],
            check=True,
            capture_output=True,
            text=True,
        )
        with tarfile.open(archive, "r:") as bundle:
            bundle.extractall(stage, filter="data")
        relative_package = PACKAGE_ROOT.relative_to(REPO_ROOT)
        reconstructed_package = stage / relative_package
        if not (reconstructed_package / "curunir_workbench").is_dir():
            raise ReconstructionError("committed archive does not contain Curunir")
        if (reconstructed_package / "argus").exists():
            raise ReconstructionError("external kernel unexpectedly exists in tracked state")
        reconstructed_requirements = reconstructed_package / "requirements.txt"
        if _sha256(reconstructed_requirements) != expected_requirements:
            raise ReconstructionError("archived requirements.txt does not match the manifest")
        _copy_kernel(kernel, reconstructed_package / "argus", sources)
        mounted_hash, mounted_count, _ = kernel_identity(reconstructed_package / "argus")
        if mounted_hash != actual_hash or mounted_count != actual_count:
            raise ReconstructionError("mounted kernel identity changed while copying")

        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
        smoke = subprocess.run(
            [
                sys.executable,
                "-I",
                str(reconstructed_package / "tools" / "reconstruction_smoke.py"),
                str(reconstructed_package),
            ],
            cwd=stage,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if smoke.returncode != 0:
            raise ReconstructionError(
                f"isolated reconstruction smoke failed\nstdout:\n{smoke.stdout}\nstderr:\n{smoke.stderr}"
            )
        smoke_result = json.loads(smoke.stdout.strip())
        if smoke_result.get("status") != "RECONSTRUCTION_OK":
            raise ReconstructionError("isolated reconstruction did not return its completion marker")
        os.replace(stage, output)
        return {
            "status": "RECONSTRUCTION_OK",
            "commit": resolved_commit,
            "kernel_tree_sha256": actual_hash,
            "kernel_python_file_count": actual_count,
            "requirements_sha256": expected_requirements,
            "output": str(output.resolve()),
            "smoke": smoke_result,
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    try:
        result = reconstruct(kernel=args.kernel.resolve(), output=args.output.resolve(), ref=args.ref)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, ReconstructionError) as exc:
        print(f"RECONSTRUCTION_REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

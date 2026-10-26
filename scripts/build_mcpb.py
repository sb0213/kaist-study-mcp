"""Build shareable source and MCPB archives from an explicit source allowlist."""

from __future__ import annotations

import json
import hashlib
import re
import tomllib
import zipfile
from pathlib import Path


def project_version(root: Path) -> str:
    manifest = json.loads((root / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = manifest["version"]
    package = (root / "src" / "kaist_study" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', package, re.M)
    if not match or version != match.group(1) or version != project["project"]["version"]:
        raise ValueError("Package, implementation and bundle versions must match")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid bundle version")
    return version


def package_sources(root: Path) -> dict[str, Path]:
    return {path.relative_to(root).as_posix(): path
            for path in sorted((root / "src" / "kaist_study").glob("*.py"))}


def write_archive(output: Path, entries: dict[str, Path], root: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for entry, path in entries.items():
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"Source outside release root: {entry}")
            bundle.write(path, arcname=entry)
    with zipfile.ZipFile(output) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != set(entries):
            raise ValueError("Bundle verification failed")


def build_bundle(root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    version = project_version(root)
    output_dir = root / "dist"
    output_dir.mkdir(exist_ok=True)
    shared = package_sources(root)
    shared.update({"pyproject.toml": root / "pyproject.toml", "install.py": root / "install.py",
                   "scripts/diagnose_stdio.py": root / "scripts" / "diagnose_stdio.py"})
    entries = dict(shared)
    entries.update({name: root / "mcpb" / name for name in ("server/entry.py", "manifest.json", "README.md")})
    output = output_dir / f"kaist-study-{version}.mcpb"
    write_archive(output, entries, root)
    source = dict(shared)
    source.update({name: root / name for name in ("README.md", ".gitignore", ".env.example")})
    for directory in ("scripts", "tests"):
        source.update({path.relative_to(root).as_posix(): path for path in sorted((root / directory).glob("*.py"))})
    source.update({"mcpb/manifest.json": root / "mcpb" / "manifest.json",
                   "mcpb/README.md": root / "mcpb" / "README.md",
                   "mcpb/server/entry.py": root / "mcpb" / "server" / "entry.py"})
    workflow = root / ".github" / "workflows" / "verify.yml"
    if workflow.is_file():
        source[".github/workflows/verify.yml"] = workflow
    source_zip = output_dir / f"kaist-study-{version}-source.zip"
    write_archive(source_zip, {f"kaist-study-{version}/{key}": value for key, value in source.items()}, root)
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in (output, source_zip))
    (output_dir / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")
    return output


if __name__ == "__main__":
    print(build_bundle())

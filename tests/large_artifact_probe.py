"""Opt-in end-to-end large-wheel probe. Run with --torch for real PyPI evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

from scriptkit.contracts import (
    Artifact,
    ArtifactPolicy,
    CatalogRelease,
    CommandSpec,
    DependencyLock,
    LockedPackage,
    Platform,
    PythonRequirement,
    ToolRelease,
    ToolSpec,
)
from scriptkit.manager import Installer
from scriptkit.registry import Registry, RegistryStore, Resolver, VerifiedCache
from scriptkit.registry.source import RegistryArtifactSource

TORCH_NAME = "torch-2.14.1-cp313-cp313-manylinux_2_28_x86_64.whl"
TORCH_HASH = "c8f71aabc67bcbfc9373dc131537a5968d04edce73e88add21354a7cd0a76985"
POLICY = ArtifactPolicy(
    max_archive_bytes=2 * 1024**3,
    max_expanded_bytes=4 * 1024**3,
    max_entries=50000,
    max_expansion_ratio=2000,
    command_timeout=1800,
)


def synthetic(root: Path, *, large: bool = True, torch: bool = False) -> Path:
    path = root / "probe-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        out.writestr(
            "probe.py",
            "def main():\n"
            + ("    import torch\n    print(torch.__version__)\n" if torch else "")
            + "    print('large artifact smoke passed')\n",
        )
        out.writestr(
            "probe-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: probe\nVersion: 1.0.0\n"
        )
        out.writestr(
            "probe-1.0.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        out.writestr("probe-1.0.0.dist-info/RECORD", "")
        if large:
            # Stored member crosses the registry's historical 64 MiB download cap.
            stored = zipfile.ZipInfo("probe_data/stored")
            with out.open(stored, "w", force_zip64=True) as member:
                for _ in range(65):
                    member.write(b"y" * 1024**2)
            for i in range(13043):
                out.writestr(f"probe_data/{i}", "x")
            with out.open("probe_data/large", "w", force_zip64=True) as member:
                for _ in range(140):
                    member.write(b"x" * 1024**2)
    return path


def exercise(root: Path, wheels: list[Path], *, smoke_module: str = "probe") -> Path:
    directory = root / "artifacts"
    directory.mkdir()
    items = []
    packages = []
    for path in wheels:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        item = Artifact(path.name, digest, path.stat().st_size)
        if path.name == TORCH_NAME:
            assert (digest, item.size) == (TORCH_HASH, 554618164)
        # Move avoids a second full artifact copy in the evidence workspace.
        path.rename(directory / digest)
        items.append(item)
        with zipfile.ZipFile(directory / digest) as archive:
            name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            metadata = BytesParser().parsebytes(archive.read(name))
        import re

        packages.append(
            LockedPackage(
                re.sub(r"[-_.]+", "-", metadata["Name"].lower()), metadata["Version"], item
            )
        )
    host = Platform(
        {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()],
        {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}[
            platform.machine().lower()
        ],
    )
    python = PythonRequirement("3.11.0", "3.99.0")
    tool = ToolSpec(
        1,
        "probe",
        "1.0.0",
        "large artifact probe",
        f"{smoke_module}:main",
        python,
        (host,),
        (CommandSpec(1, "probe", "help", ()),),
    )
    main = next(a for a in items if a.path.startswith("probe-"))
    catalog = CatalogRelease(
        1,
        "probe",
        "1.0.0",
        (
            ToolRelease(
                tool,
                main,
                (
                    DependencyLock(
                        1, host, python, "pip", tuple(p for p in packages if p.artifact != main)
                    ),
                ),
            ),
        ),
    )
    raw = catalog.canonical_json().encode()
    (root / "locked-catalog.json").write_bytes(raw)
    pin = Artifact("catalog.json", hashlib.sha256(raw).hexdigest(), len(raw))
    cache = VerifiedCache(root / "cache")
    cache.root.mkdir()
    (cache.root / pin.sha256).write_bytes(raw)
    origin = "https://probe.example/"
    registry = Registry(1, "probe", origin, pin, 9999999999)
    store = RegistryStore(root / "registries.json")
    store.add(registry, consent_origin=origin)
    resolver = Resolver(store, cache, policy=POLICY, artifact_directory=directory)
    plan = resolver.resolve(
        "probe/probe@1.0.0",
        platform=host,
        python_version=platform.python_version(),
        generation="first",
        destination="probe",
        offline=True,
    )
    source = RegistryArtifactSource(
        resolver, "probe", offline=True, policy=POLICY, artifact_directory=directory
    )
    manager = Installer(root / "state", root / "bin", source, policy=POLICY)
    receipt = manager.install(plan)
    assert receipt is not None
    print(
        json.dumps(
            {
                "receipt": str(receipt),
                "artifacts": len(items),
                "bytes": sum(a.size for a in items),
                "torch_sha256": TORCH_HASH if any(a.path == TORCH_NAME for a in items) else None,
            }
        )
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--torch", action="store_true")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    wheels = []
    if args.torch:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "--index-url",
                "https://pypi.org/simple",
                "--dest",
                str(args.directory),
                "torch==2.14.1",
            ],
            check=True,
        )
        wheels = list(args.directory.glob("*.whl"))
        if not any(p.name == TORCH_NAME for p in wheels):
            raise RuntimeError("run on Linux x86_64 CPython 3.13 for the exact target wheel")
    wheels.append(synthetic(args.directory, large=not args.torch, torch=args.torch))
    exercise(args.directory, wheels)


if __name__ == "__main__":
    main()

"""Build twice from clean git archives, validate resources, and record SHA256s.

Normalizes sdist timestamps/ownership (setuptools does not honor SOURCE_DATE_EPOCH
for tar headers). Wheels are built from that sdist in isolated build environments.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def run(*args: str, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def normalize_sdist(path: Path, epoch: int) -> None:
    buffer = io.BytesIO()
    with tarfile.open(path) as source, tarfile.open(fileobj=buffer, mode="w") as target:
        for member in sorted(source.getmembers(), key=lambda item: item.name):
            member.mtime = epoch
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.pax_headers = {}
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
    path.write_bytes(gzip.compress(buffer.getvalue(), mtime=0))


def hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.name.endswith((".whl", ".tar.gz", ".py", ".sh", ".ps1"))
    }


def validate_resources(wheel: Path, root: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for source in (root / "src" / "scriptkit").rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts:
                relative = source.relative_to(root / "src").as_posix()
                if relative not in names:
                    raise ValueError(f"Wheel missing package resource: {relative}")
        if not any(name.endswith("/licenses/LICENSE") for name in names):
            raise ValueError("Wheel missing license")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    epoch = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=root))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch), "PYTHONHASHSEED": "0"}
    with tempfile.TemporaryDirectory(prefix="scriptkit-release-") as temporary:
        work = Path(temporary)
        archive = work / "source.zip"
        run("git", "archive", "--format=zip", f"--output={archive}", "HEAD", cwd=root, env=env)
        results = []
        for index in range(2):
            clean = work / str(index)
            with zipfile.ZipFile(archive) as source:
                source.extractall(clean)
            run(sys.executable, "-m", "build", "--sdist", cwd=clean, env=env)
            sdist = next((clean / "dist").glob("*.tar.gz"))
            normalize_sdist(sdist, epoch)
            unpack = clean / "unpack"
            with tarfile.open(sdist) as source:
                source.extractall(unpack, filter="data")
            extracted = next(unpack.iterdir())
            run(
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(clean / "dist"),
                cwd=extracted,
                env=env,
            )
            wheel = next((clean / "dist").glob("*.whl"))
            validate_resources(wheel, clean)
            run(
                sys.executable,
                "-m",
                "twine",
                "check",
                "--strict",
                str(sdist),
                str(wheel),
                cwd=clean,
                env=env,
            )
            for installer in ("install.sh", "install.ps1"):
                shutil.copy2(clean / installer, clean / "dist" / installer)
            shutil.copy2(clean / "src/scriptkit/bootstrap.py", clean / "dist/bootstrap.py")
            results.append(hashes(clean / "dist"))
        if results[0] != results[1]:
            raise ValueError(f"Non-reproducible release: {results}")
        destination = root / "dist"
        destination.mkdir(exist_ok=True)
        if any(destination.iterdir()):
            raise ValueError("dist must be empty; refusing to mix release artifacts")
        for name in results[1]:
            shutil.copy2(clean / "dist" / name, destination / name)
        (destination / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in results[1].items()), encoding="utf-8"
        )
        (destination / "build.json").write_text(
            json.dumps(
                {"commit": commit, "source_date_epoch": epoch, "sha256": results[1]}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        print("Reproducibility verified:", results[1])


if __name__ == "__main__":
    main()

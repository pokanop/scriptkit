"""Match trusted dependency identities to wheel metadata before backend execution."""

from __future__ import annotations

import io
import re
import zipfile
from email.parser import BytesParser
from pathlib import Path

from scriptkit.contracts.models import Artifact, InstallPlan


def verify_locks(
    plan: InstallPlan, artifacts: dict[Artifact, bytes] | dict[Artifact, Path]
) -> None:
    for lock in plan.release.locks:
        if lock.platform != plan.platform:
            continue
        for package in lock.packages:
            if not package.artifact.path.endswith(".whl"):
                raise ValueError("Python dependencies must be wheels")
            raw = artifacts[package.artifact]
            with zipfile.ZipFile(io.BytesIO(raw) if isinstance(raw, bytes) else raw) as archive:
                entries = [p for p in archive.namelist() if p.endswith(".dist-info/METADATA")]
                if len(entries) != 1:
                    raise ValueError("wheel must contain one distribution metadata file")
                with archive.open(entries[0]) as stream:
                    data = stream.read(1024 * 1024 + 1)
                    if len(data) > 1024 * 1024:
                        raise ValueError("wheel metadata exceeds 1 MiB policy")
                    metadata = BytesParser().parsebytes(data)
            if (
                len(metadata.get_all("Name", [])) != 1
                or len(metadata.get_all("Version", [])) != 1
                or re.sub(r"[-_.]+", "-", str(metadata["Name"]).lower()) != package.name
                or str(metadata["Version"]) != package.version
            ):
                raise ValueError("wheel identity does not match dependency lock")

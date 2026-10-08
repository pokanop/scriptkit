"""Match trusted dependency identities to wheel metadata before backend execution."""

from __future__ import annotations

import io
import re
import zipfile
from email.parser import BytesParser

from scriptkit.contracts.models import Artifact, InstallPlan


def verify_locks(plan: InstallPlan, artifacts: dict[Artifact, bytes]) -> None:
    for lock in plan.release.locks:
        if lock.platform != plan.platform:
            continue
        for package in lock.packages:
            if not package.artifact.path.endswith(".whl"):
                raise ValueError("Python dependencies must be wheels")
            with zipfile.ZipFile(io.BytesIO(artifacts[package.artifact])) as archive:
                entries = [p for p in archive.namelist() if p.endswith(".dist-info/METADATA")]
                if len(entries) != 1:
                    raise ValueError("wheel must contain one distribution metadata file")
                metadata = BytesParser().parsebytes(archive.read(entries[0]))
            if (
                len(metadata.get_all("Name", [])) != 1
                or len(metadata.get_all("Version", [])) != 1
                or re.sub(r"[-_.]+", "-", str(metadata["Name"]).lower()) != package.name
                or str(metadata["Version"]) != package.version
            ):
                raise ValueError("wheel identity does not match dependency lock")

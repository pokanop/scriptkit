"""Validate without extraction or execution; installers must retain confinement."""

from __future__ import annotations

import io
import re
import stat
from pathlib import Path

from scriptkit.contracts.artifacts import ArtifactPolicy, DEFAULT_ARTIFACT_POLICY
import zipfile
import zlib

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.models import INVENTORY_PATH


def _safe_member_name(name: str, *, allow_spaces: bool) -> bool:
    if not allow_spaces or " " not in name:
        return re.fullmatch(INVENTORY_PATH, name) is not None
    # Reuse the existing grammar without widening catalog or transport paths.
    # Spaces must have an allowed non-space character on both sides, within
    # one segment. Normalization is validation-only; archive names stay intact.
    for index, char in enumerate(name):
        if char == " " and (
            index == 0
            or index == len(name) - 1
            or re.fullmatch(r"[A-Za-z0-9_.+@-]", name[index - 1]) is None
            or re.fullmatch(r"[A-Za-z0-9_.+@-]", name[index + 1]) is None
        ):
            return False
    for segment in name.split("/"):
        prefix = re.split(r"[ .]", segment, maxsplit=1)[0]
        if re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", prefix, re.IGNORECASE):
            return False
    return re.fullmatch(INVENTORY_PATH, name.replace(" ", "_")) is not None


def validate_archive(
    path: str,
    raw: bytes | Path,
    *,
    max_expanded: int = 128 * 1024 * 1024,
    policy: ArtifactPolicy | None = None,
) -> str:
    budget = policy or DEFAULT_ARTIFACT_POLICY
    if policy is not None:
        max_expanded = policy.max_expanded_bytes
        size = len(raw) if isinstance(raw, bytes) else raw.stat().st_size
        if size > policy.max_archive_bytes:
            raise ContractError("archive bytes exceed policy")
    if path.endswith(".whl"):
        kind = "wheel"
    elif path.endswith(".scripts.zip"):
        kind = "legacy-scripts"
    else:
        raise ContractError("only wheels and .scripts.zip legacy bundles are supported")
    try:
        with zipfile.ZipFile(io.BytesIO(raw) if isinstance(raw, bytes) else raw) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > budget.max_entries:
                raise ContractError("archive entry count outside policy")
            seen: set[str] = set()
            files: set[str] = set()
            total = 0
            for item in infos:
                name = item.filename.rstrip("/") if item.is_dir() else item.filename
                folded = name.casefold()
                mode = item.external_attr >> 16
                if (
                    item.orig_filename != item.filename
                    or not _safe_member_name(name, allow_spaces=budget.allow_member_spaces)
                    or folded in seen
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or item.flag_bits & 1
                    or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                ):
                    raise ContractError("unsafe archive member")
                seen.add(folded)
                if not item.is_dir():
                    files.add(folded)
                    if kind == "legacy-scripts" and not name.endswith(".py"):
                        raise ContractError("legacy bundles contain only Python source files")
                total += item.file_size
                if total > max_expanded:
                    raise ContractError("expanded archive exceeds policy")
                if item.file_size > max(1, item.compress_size) * budget.max_expansion_ratio:
                    raise ContractError("archive expansion ratio exceeds policy")
                # Bounded reads validate full CRC without materializing large members.
                with archive.open(item) as stream:
                    actual = 0
                    while chunk := stream.read(1024 * 1024):
                        actual += len(chunk)
                        if actual > item.file_size:
                            raise ContractError("archive member size mismatch")
                    if actual != item.file_size:
                        raise ContractError("archive member size mismatch")
            if any(
                "/".join(name.split("/")[:i]) in files
                for name in seen
                for i in range(1, len(name.split("/")))
            ):
                raise ContractError("archive file/directory collision")
            if kind == "wheel" and not any(name.endswith(".dist-info/wheel") for name in files):
                raise ContractError("wheel metadata missing")
    except ContractError:
        raise
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
        EOFError,
        ValueError,
    ) as exc:
        raise ContractError("invalid archive") from exc
    return kind

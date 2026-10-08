"""Validate without extraction or execution; installers must retain confinement."""

from __future__ import annotations

import io
import re
import stat
import zipfile
import zlib

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.models import INVENTORY_PATH


def validate_archive(path: str, raw: bytes, *, max_expanded: int = 128 * 1024 * 1024) -> str:
    if path.endswith(".whl"):
        kind = "wheel"
    elif path.endswith(".scripts.zip"):
        kind = "legacy-scripts"
    else:
        raise ContractError("only wheels and .scripts.zip legacy bundles are supported")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 10000:
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
                    or re.fullmatch(INVENTORY_PATH, name) is None
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
                # Read every member to validate CRC and actual expansion before acceptance.
                with archive.open(item) as stream:
                    if len(stream.read(item.file_size + 1)) != item.file_size:
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

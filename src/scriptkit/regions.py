"""Receipt-owned managed regions, opt-in replacement for legacy text helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .state import LocalStateIO, StateConflict, StateIO


@dataclass(frozen=True)
class RegionReceipt:
    """Persist alongside the install receipt; content is non-secret configuration."""

    owner: str
    block: bytes
    separator: bytes
    created: bool = False


class OwnedRegion:
    """Only edit a complete, unmodified region proven by a caller-held receipt.

    Marker-looking substrings in foreign lines are not markers. Duplicate, partial,
    nested or edited owned regions fail closed. Foreign bytes (including CRLF and
    the original EOF newline state) survive removal. No backup file is overwritten.
    """

    def __init__(self, owner: str, *, io: StateIO | None = None) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", owner):
            raise ValueError("invalid region owner")
        self.owner = owner
        self.begin = f"# >>> scriptkit:{owner} >>>".encode()
        self.end = f"# <<< scriptkit:{owner} <<<".encode()
        self.io = LocalStateIO() if io is None else io

    def _span(self, text: bytes) -> tuple[int, int] | None:
        starts, ends = [], []
        offset = 0
        for line in text.splitlines(keepends=True):
            if line.strip() == self.begin:
                starts.append(offset)
            if line.strip() == self.end:
                ends.append(offset + len(line))
            offset += len(line)
        if not starts and not ends:
            return None
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise StateConflict("ambiguous managed region")
        return starts[0], ends[0]

    def _owned(self, text: bytes, receipt: RegionReceipt | None) -> tuple[int, int] | None:
        span = self._span(text)
        if span is None:
            if receipt is not None:
                raise StateConflict("owned region is missing")
            return None
        if receipt is None or receipt.owner != self.owner:
            raise StateConflict("existing region has no matching ownership receipt")
        start, stop = span
        sep = receipt.separator
        if (
            sep not in (b"", b"\n")
            or text[start:stop] != receipt.block
            or start < len(sep)
            or text[start - len(sep) : start] != sep
        ):
            raise StateConflict("owned region was edited")
        return start - len(sep), stop

    def apply(
        self, path: Path, body: str, *, receipt: RegionReceipt | None = None
    ) -> RegionReceipt:
        raw = self.io.read(path)
        text = raw or b""
        span = self._owned(text, receipt)
        content = body.encode("utf-8").strip(b"\r\n")
        if any(line.strip() in (self.begin, self.end) for line in content.splitlines()):
            raise ValueError("body contains reserved marker")
        block = self.begin + b"\n" + (content + b"\n" if content else b"") + self.end + b"\n"
        separator = receipt.separator if receipt else (b"\n" if text else b"")
        inserted = separator + block
        updated = text[: span[0]] + inserted + text[span[1] :] if span else text + inserted
        if updated != text:
            self.io.replace(path, updated, expected=raw, mode=None)
        created = receipt.created if receipt else raw is None
        return RegionReceipt(self.owner, block, separator, created)

    def clear(self, path: Path, *, receipt: RegionReceipt) -> bool:
        raw = self.io.read(path)
        text = raw or b""
        if self._span(text) is None:
            return False
        span = self._owned(text, receipt)
        assert span is not None
        updated = text[: span[0]] + text[span[1] :]
        if receipt.created and not updated:
            assert raw is not None
            self.io.remove(path, expected=raw)
        else:
            self.io.replace(path, updated, expected=raw, mode=None)
        return True

"""Bounded HTTPS transport and content-addressed, reverified cache."""

from __future__ import annotations

import hashlib
import re
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from typing import Protocol

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.models import INVENTORY_PATH, Artifact
from scriptkit.state import LocalStateIO, StateConflict, StateIO

from .trust import origin_url


class Transport(Protocol):
    def fetch(self, url: str, limit: int) -> bytes: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        raise ContractError("registry redirects are forbidden")


class HTTPTransport:
    def fetch(self, url: str, limit: int) -> bytes:
        parsed = urlsplit(url)
        origin_url(f"{parsed.scheme}://{parsed.netloc}/")
        if (
            parsed.query
            or parsed.fragment
            or re.fullmatch(INVENTORY_PATH, parsed.path.lstrip("/")) is None
            or parsed.path.startswith("//")
            or limit < 0
        ):
            raise ContractError("unsafe download URL or limit")
        opener = urllib.request.build_opener(NoRedirect())
        request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with opener.open(request, timeout=30) as response:
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ContractError("encoded responses are forbidden")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise ContractError("oversized or malformed response length")
            data: bytes = response.read(limit + 1)
        if len(data) > limit:
            raise ContractError("oversized response")
        return data


class VerifiedCache:
    def __init__(
        self,
        root: Path,
        transport: Transport | None = None,
        io: StateIO | None = None,
        *,
        max_bytes: int = 64 * 1024 * 1024,
    ):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.root = root
        self.transport = transport or HTTPTransport()
        self.io = io or LocalStateIO()
        self.max_bytes = max_bytes

    def get(self, origin: str, artifact: Artifact, *, offline: bool = False) -> bytes:
        origin_url(origin)
        if artifact.size > self.max_bytes:
            raise ContractError("artifact exceeds download policy")
        path = self.root / artifact.sha256
        raw = self.io.read(path)
        if raw is None:
            if offline:
                raise ContractError("verified cache incomplete; offline download forbidden")
            raw = self.transport.fetch(origin + artifact.path, artifact.size)
            self._verify(raw, artifact)
            try:
                self.io.replace(path, raw, expected=None)
            except StateConflict:
                # Another writer won or still holds the publication lock. Return
                # our verified bytes; cache persistence is best-effort, not trust.
                pass
        else:
            self._verify(raw, artifact)
        return raw

    @staticmethod
    def _verify(raw: bytes, artifact: Artifact) -> None:
        if len(raw) != artifact.size or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ContractError("artifact size or SHA-256 mismatch")

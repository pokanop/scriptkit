"""Regression probes for registry review: malformed ZIPs and concurrent fill."""

import io
import struct
import zipfile
import zlib

import pytest

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.ports import ArtifactSource
from scriptkit.registry import RegistryArtifactSource
from scriptkit.registry.archives import validate_archive
from scriptkit.state import LocalStateIO, StateConflict

from test_registry import ORIGIN, artifact, fixture as fixture


@pytest.mark.parametrize("corruption", ["deflate", "utf8"])
def test_malformed_archive_error_contract(corruption):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tool.py", b"print('hello world')")
    raw = bytearray(stream.getvalue())
    if corruption == "deflate":
        raw[30 + len("tool.py")] = 7  # reserved DEFLATE block type
        expected = zlib.error
    else:
        central = raw.index(b"PK\x01\x02")
        struct.pack_into("<H", raw, 6, 0x800)
        struct.pack_into("<H", raw, central + 8, 0x800)
        raw = raw.replace(b"tool.py", b"\xffool.py")
        expected = UnicodeDecodeError
    with pytest.raises(ContractError, match="invalid archive") as error:
        validate_archive("tool.scripts.zip", bytes(raw))
    assert isinstance(error.value.__cause__, expected)


@pytest.mark.parametrize("winner_finished", [True, False])
def test_concurrent_cache_publication(fixture, winner_finished):
    store, cache, resolver, registration, transport = fixture

    class RacingIO(LocalStateIO):
        def replace(self, path, data, *, expected, mode=0o600):
            if winner_finished:
                super().replace(path, data, expected=expected, mode=mode)
                # Actual CAS conflict after the other caller publishes.
                super().replace(path, data, expected=expected, mode=mode)
            else:
                raise StateConflict("writer still holds lock")

    cache.io = RacingIO()
    assert cache.get(ORIGIN, registration.catalog) == transport.data[ORIGIN + "catalog.json"]
    if winner_finished:
        assert cache.get(ORIGIN, registration.catalog, offline=True)
    else:
        with pytest.raises(ContractError, match="incomplete"):
            cache.get(ORIGIN, registration.catalog, offline=True)


def test_artifact_source_port(fixture):
    store, cache, resolver, registration, transport = fixture
    source: ArtifactSource = RegistryArtifactSource(resolver, registration.namespace)
    catalog = source.catalog("local-catalog", "1.0.0")
    target = catalog.releases[0].artifact
    assert source.fetch(target) == transport.data[ORIGIN + target.path]
    offline: ArtifactSource = RegistryArtifactSource(resolver, registration.namespace, offline=True)
    transport.data.clear()
    assert offline.catalog("local-catalog", "1.0.0") == catalog
    assert offline.fetch(target)
    with pytest.raises(ContractError, match="namespace"):
        source.catalog("other", "1.0.0")
    with pytest.raises(ContractError, match="exact catalog version"):
        source.catalog("local-catalog", "latest")
    with pytest.raises(ContractError, match="not authorized"):
        source.fetch(artifact("other.whl", b"unexpected"))
    resolver.clock = lambda: 200
    with pytest.raises(ContractError, match="expired"):
        offline.fetch(target)
    resolver.clock = lambda: 100
    store.remove(registration.namespace)
    with pytest.raises(ContractError, match="unknown"):
        offline.fetch(target)

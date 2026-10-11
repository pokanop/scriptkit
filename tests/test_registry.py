from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import replace
from importlib.resources import files

import pytest

from scriptkit.contracts.codec import ContractError
from scriptkit.contracts.models import Artifact, Platform
from scriptkit.registry import Registry, RegistryStore, Resolver, VerifiedCache
from scriptkit.registry.archives import validate_archive
from scriptkit.registry.cache import HTTPTransport, NoRedirect

ORIGIN = "https://fixture.example/releases/"


def archive(entries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        for name, data in (
            entries
            or {
                "tool.py": b"raise RuntimeError('never execute')",
                "tool.dist-info/WHEEL": b"Wheel-Version: 1.0",
            }
        ).items():
            output.writestr(name, data)
    return stream.getvalue()


def artifact(path, raw):
    return Artifact(path, hashlib.sha256(raw).hexdigest(), len(raw))


class FixtureTransport:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def fetch(self, url, limit):
        self.calls.append(url)
        return self.data[url]


@pytest.fixture
def fixture(tmp_path):
    wheel = archive()
    tool = artifact("tool.whl", wheel)
    dependency = artifact("dependency.whl", archive({"dep.dist-info/WHEEL": b"Wheel-Version: 1.0"}))
    catalog = json.loads(
        files("scriptkit.contracts.resources").joinpath("CatalogRelease.example.json").read_text()
    )
    catalog["releases"][0]["artifact"] = tool.to_dict()
    catalog["releases"][0]["locks"][0]["packages"] = [
        {"name": "dependency", "version": "1.0.0", "artifact": dependency.to_dict()}
    ]
    raw = json.dumps(catalog).encode()
    registration = Registry(1, "local-catalog", ORIGIN, artifact("catalog.json", raw), 200)
    transport = FixtureTransport(
        {
            ORIGIN + "catalog.json": raw,
            ORIGIN + tool.path: wheel,
            ORIGIN + dependency.path: archive({"dep.dist-info/WHEEL": b"Wheel-Version: 1.0"}),
        }
    )
    store = RegistryStore(tmp_path / "registries.json")
    store.add(registration, consent_origin=ORIGIN)
    cache = VerifiedCache(tmp_path / "cache", transport)
    resolver = Resolver(store, cache, clock=lambda: 100)
    return store, cache, resolver, registration, transport


def resolve(resolver, **kwargs):
    return resolver.resolve(
        "local-catalog/example-tool@1.0.0",
        platform=Platform("linux", "x86_64"),
        python_version="3.11.0",
        generation="first",
        destination="tools/example",
        **kwargs,
    )


def test_fixture_online_and_offline(fixture):
    store, cache, resolver, registration, transport = fixture
    assert resolver.list("local-catalog") == ("local-catalog/example-tool@1.0.0",)
    plan = resolve(resolver)
    assert plan.registry.origin == ORIGIN
    assert plan.installation.catalog_sha256 == registration.catalog.sha256
    assert plan.installation.release.tool.version == "1.0.0"
    assert len(plan.artifacts) == 2 and len(plan.lock_sha256) == 1
    assert type(plan).from_json(plan.canonical_json()) == plan
    with pytest.raises(ContractError, match="provenance"):
        replace(plan, lock_sha256=("0" * 64,))
    transport.data.clear()
    assert resolve(resolver, offline=True) == plan
    assert len(transport.calls) == 3
    store.remove("local-catalog")
    assert store.list() == ()
    with pytest.raises(ContractError, match="unknown"):
        store.remove("local-catalog")


def test_consent_and_shadowing(fixture):
    store, cache, resolver, registration, transport = fixture
    with pytest.raises(ContractError, match="consent"):
        store.add(registration, consent_origin="")
    with pytest.raises(ContractError, match="registry 'local-catalog' is already registered"):
        store.add(
            replace(registration, origin="https://evil.example/"),
            consent_origin="https://evil.example/",
        )
    store.remove(registration.namespace)
    store.add(replace(registration, namespace="other"), consent_origin=ORIGIN)
    with pytest.raises(ContractError, match="shadowing"):
        resolver.list("other")


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com/",
        "file:///tmp/",
        "https://user:pass@example.com/",
        "https://example.com/?a=1",
        "https://example.com/#x",
        "https://example.com/%2e/",
        "https://example.com/../",
        "https://example.com",
        "https://EXAMPLE.com/",
        "https://example.com:99999/",
    ],
)
def test_insecure_origins(origin):
    with pytest.raises(ContractError):
        Registry(1, "example", origin, artifact("catalog.json", b"{}"), 1)


def test_expiry_unknown_and_exact_version(fixture):
    store, cache, resolver, registration, transport = fixture
    with pytest.raises(ContractError, match="expired"):
        Resolver(store, cache, clock=lambda: 200).list("local-catalog", offline=True)
    with pytest.raises(ContractError, match="unknown"):
        resolver.list("missing")
    for target in [
        "example-tool",
        "local-catalog/example-tool@latest",
        "local-catalog/example-tool@01.0.0",
    ]:
        with pytest.raises(ContractError):
            resolver.resolve(
                target,
                platform=Platform("linux", "x86_64"),
                python_version="3.11.0",
                generation="first",
                destination="tools/example",
            )
    with pytest.raises(ContractError):
        Registry.from_dict({**registration.to_dict(), "schema_version": 2})


def test_cache_policy_and_tampering(fixture):
    store, cache, resolver, registration, transport = fixture
    with pytest.raises(ContractError, match="incomplete"):
        resolve(resolver, offline=True)
    with pytest.raises(ValueError):
        VerifiedCache(cache.root, max_bytes=0)
    with pytest.raises(ContractError, match="policy"):
        VerifiedCache(cache.root, max_bytes=1).get(ORIGIN, registration.catalog)
    transport.data[ORIGIN + "catalog.json"] += b" "
    with pytest.raises(ContractError, match="mismatch"):
        resolver.list("local-catalog")
    transport.data[ORIGIN + "catalog.json"] = transport.data[ORIGIN + "catalog.json"][:-1]
    resolve(resolver)
    (cache.root / registration.catalog.sha256).write_bytes(b"altered locks")
    with pytest.raises(ContractError, match="mismatch"):
        resolve(resolver, offline=True)


@pytest.mark.parametrize("change", ["version", "hook", "lock"])
def test_strict_catalog(fixture, change):
    store, cache, resolver, registration, transport = fixture
    data = json.loads(transport.data[ORIGIN + "catalog.json"])
    if change == "version":
        data["releases"][0]["tool"]["version"] = "latest"
    elif change == "hook":
        data["install_hook"] = "echo unsafe"
    else:
        data["releases"][0]["locks"][0]["packages"][0]["artifact"]["path"] = "../evil.whl"
    raw = json.dumps(data).encode()
    store.remove(registration.namespace)
    store.add(replace(registration, catalog=artifact("catalog.json", raw)), consent_origin=ORIGIN)
    transport.data[ORIGIN + "catalog.json"] = raw
    with pytest.raises(ContractError):
        resolver.list("local-catalog")


@pytest.mark.parametrize(
    "name", ["../evil.py", "/evil.py", "C:/evil.py", "a\\evil.py", "CON.py", "a./evil.py"]
)
def test_archive_paths(name):
    # ZipInfo normalizes backslashes while writing on Windows. Patch both ZIP
    # headers after creation so every platform receives the same hostile bytes.
    safe_name = name.replace("\\", "/")
    raw = archive({safe_name: b"pass"}).replace(safe_name.encode(), name.encode())
    with pytest.raises(ContractError):
        validate_archive("bundle.scripts.zip", raw)


def test_archive_policy():
    assert validate_archive("bundle.scripts.zip", archive({"tool.py": b"pass"})) == "legacy-scripts"
    for path, data in [
        ("tool.tar.gz", b""),
        ("tool.whl", b"broken"),
        ("tool.whl", archive({"tool.py": b"pass"})),
        ("tool.scripts.zip", archive({"hook.sh": b"evil"})),
        ("tool.whl", archive({"a": b"", "a/b": b""})),
        ("tool.whl", archive({"a": b"", "A": b""})),
    ]:
        with pytest.raises(ContractError):
            validate_archive(path, data)
    with pytest.raises(ContractError, match="expanded"):
        validate_archive("tool.whl", archive(), max_expanded=1)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        link = zipfile.ZipInfo("link.py")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, "outside")
    with pytest.raises(ContractError):
        validate_archive("tool.scripts.zip", stream.getvalue())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w"):
        pass
    with pytest.raises(ContractError):
        validate_archive("tool.whl", stream.getvalue())


def test_interrupted_download(fixture):
    store, cache, resolver, registration, transport = fixture

    def interrupt(url, limit):
        raise KeyboardInterrupt

    transport.fetch = interrupt
    with pytest.raises(KeyboardInterrupt):
        resolver.list("local-catalog")
    assert not cache.root.exists()


@pytest.mark.parametrize(
    "headers,data",
    [
        ({"Content-Encoding": "gzip"}, b""),
        ({"Content-Length": "100"}, b""),
        ({"Content-Length": "bad"}, b""),
        ({}, b"1234"),
        ({"Content-Length": "3"}, b"123"),
    ],
)
def test_bounded_transport(monkeypatch, headers, data):
    class Response(io.BytesIO):
        pass

    class Opener:
        def open(self, request, timeout):
            assert timeout == 30
            response = Response(data)
            response.headers = headers
            return response

    def build_opener(*handlers):
        assert len(handlers) == 1 and isinstance(handlers[0], NoRedirect)
        return Opener()

    monkeypatch.setattr("urllib.request.build_opener", build_opener)
    if data == b"123":
        assert HTTPTransport().fetch(ORIGIN + "catalog.json", 3) == data
    else:
        with pytest.raises(ContractError):
            HTTPTransport().fetch(ORIGIN + "catalog.json", 3)
    with pytest.raises(ContractError, match="redirect"):
        NoRedirect().redirect_request(None, None, 302, "", {}, "http://evil/")

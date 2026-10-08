"""Round-one regressions for real inventories and untrusted catalog text."""

from dataclasses import replace
import json

from jsonschema import Draft202012Validator
import pytest

from scriptkit.contracts import (
    AIProposal,
    ArgumentSpec,
    Artifact,
    CatalogRelease,
    CommandSpec,
    ContractError,
    Generation,
    LockedPackage,
    Platform,
    Receipt,
    ToolSpec,
    resource_text,
)
from scriptkit.contracts import codec


def example(cls):
    return cls.from_json(resource_text(f"{cls.__name__}.example.json"))


def test_real_inventory_roundtrip():
    paths = (
        "sample.dist-info/METADATA",
        "Scripts/python.exe",
        "Lib/site-packages/PIL/Image.py",
        "LICENSE",
    )
    receipt = replace(example(Receipt), files=tuple(Artifact(p, "a" * 64, 1) for p in paths))
    assert Receipt.from_json(receipt.canonical_json()) == receipt
    Draft202012Validator(Receipt.json_schema()).validate(receipt.to_dict())


@pytest.mark.parametrize(
    "path",
    [
        "Con",
        "cOn.txt",
        "pkg/AUX.py",
        "pkg/lPt1.txt",
        "../Lib",
        "Lib/../x",
        "Lib/.",
        "Lib/X.",
        "Lib/X ",
        "Lib/\x1bx",
        "Lib/\x85x",
        "C:/Lib",
        "Lib\\X",
    ],
)
def test_inventory_rejects_unsafe_paths(path):
    with pytest.raises(ContractError):
        Artifact(path, "a" * 64, 1)
    assert not Draft202012Validator(Artifact.json_schema()).is_valid(
        dict(path=path, sha256="a" * 64, size=1)
    )


@pytest.mark.parametrize("paths", [("LICENSE", "license"), ("Lib", "lib/site.py"), ("A/B", "a/b")])
def test_inventory_casefold_collision(paths):
    with pytest.raises(ContractError, match="files.path"):
        replace(example(Receipt), files=tuple(Artifact(p, "a" * 64, 1) for p in paths))


@pytest.mark.parametrize("name", ["g++", "libstdc++6", "python@3.12", "openssl@3"])
def test_real_package_identifiers(name):
    package = LockedPackage(name, "1.0", Artifact("pkg.zip", "a" * 64, 1))
    assert LockedPackage.from_json(package.canonical_json()) == package
    Draft202012Validator(LockedPackage.json_schema()).validate(package.to_dict())


def second_receipt(first, platform, python):
    release = first.plan.release
    release = replace(
        release,
        tool=replace(release.tool, name="second", platforms=(platform,)),
        locks=tuple(replace(lock, platform=platform) for lock in release.locks),
    )
    return replace(
        first,
        plan=replace(
            first.plan,
            release=release,
            platform=platform,
            python_version=python,
            destination="tools/second",
        ),
    )


def test_generation_requires_one_platform_but_allows_isolated_python_versions():
    generation = example(Generation)
    first = generation.receipts[0]
    second = second_receipt(first, first.plan.platform, "3.12.0")
    assert Generation.from_dict(replace(generation, receipts=(first, second)).to_dict())
    for platform in (Platform("windows", "x86_64"), Platform("linux", "arm64")):
        with pytest.raises(ContractError, match="one host platform"):
            replace(generation, receipts=(first, second_receipt(first, platform, "3.12.0")))


@pytest.mark.parametrize(
    "cls,field",
    [
        (ToolSpec, "description"),
        (CommandSpec, "help"),
        (ArgumentSpec, "help"),
        (AIProposal, "rationale"),
        (AIProposal, "model"),
        (ArgumentSpec, "choices"),
    ],
)
@pytest.mark.parametrize("control", ["\x00", "\x1b[31m", "\r", "\x7f", "\x85", "\x9b"])
def test_catalog_text_rejects_controls_in_parser_and_schema(cls, field, control):
    data = example(cls).to_dict()
    data[field] = [control] if field == "choices" else control
    with pytest.raises(ContractError, match="control characters"):
        cls.from_dict(data)
    assert not Draft202012Validator(cls.json_schema()).is_valid(data)


def test_text_allows_newline_tab_and_unicode():
    tool = replace(example(ToolSpec), description="Café\n\t説明")
    assert ToolSpec.from_json(tool.canonical_json()) == tool
    Draft202012Validator(ToolSpec.json_schema()).validate(tool.to_dict())


def test_type_hints_resolved_once_per_class_on_large_catalog(monkeypatch):
    data = example(CatalogRelease).to_dict()
    release = data["releases"][0]
    data["releases"] = []
    for i in range(300):
        item = json.loads(json.dumps(release))
        item["tool"]["name"] = f"tool-{i}"
        data["releases"].append(item)
    codec._cached_hints.cache_clear()
    seen = []
    original = codec._resolve_hints

    def resolve(cls):
        seen.append(cls)
        return original(cls)

    monkeypatch.setattr(codec, "_resolve_hints", resolve)
    parsed = CatalogRelease.from_dict(data)
    assert len(parsed.releases) == 300
    assert len(seen) == len(set(seen))
    before = len(seen)
    assert CatalogRelease.from_json(parsed.canonical_json()) == parsed
    assert len(seen) == before


def test_nested_error_has_no_repeated_path_prefix():
    data = example(CatalogRelease).to_dict()
    data["releases"][0]["tool"]["name"] = "../bad"
    with pytest.raises(ContractError) as error:
        CatalogRelease.from_dict(data)
    assert str(error.value).count("CatalogRelease") == 1

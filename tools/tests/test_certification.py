"""Release evidence must fail closed on substitution or missing manifest entries."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import io
import tarfile
from packaging.version import Version
import tomllib
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("certify", ROOT / "tools/certify_release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def assets(tmp_path):
    (tmp_path / "wheel.whl").write_bytes(b"fixture")
    digest = hashlib.sha256(b"fixture").hexdigest()
    (tmp_path / "build.json").write_text("{}")
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  wheel.whl\n")
    return {
        "assets": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    }


def check_pin_identity(pins, metadata):
    assert Version(pins["version"]) <= Version(metadata["version"])
    assert (
        pins["wheel"]
        == pins["distribution"].replace("-", "_") + "-" + pins["version"] + "-py3-none-any.whl"
    )


@pytest.mark.parametrize(
    "version,name", [("1.6.0", "pokanop-scriptkit"), ("1.5.0", "renamed-fixture")]
)
def test_historical_pins_survive_version_bump_and_rename(version, name):
    pins = json.loads((ROOT / "docs/release-pins.json").read_text())
    check_pin_identity(pins, {"version": version, "name": name})


def test_extract_historical_suite_identity_and_reject_links(tmp_path):
    pins = {"distribution": "historical", "version": "1.5.0", "module": "old", "command": "old"}
    metadata = b'[project]\nname="historical"\nversion="1.5.0"\n[project.scripts]\nold="old.entrypoint:main"\n'
    sdist = tmp_path / "release.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name, content in (
            ("release/pyproject.toml", metadata),
            ("release/tests/test_old.py", b"old-only"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    suite = module.extract_suite(sdist, tmp_path / "good", pins)
    assert (suite / "tests/test_old.py").read_bytes() == b"old-only"
    with pytest.raises(ValueError, match="identity"):
        module.extract_suite(
            sdist, tmp_path / "bad-identity", {**pins, "distribution": "head-name"}
        )
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("release/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "outside"
        archive.addfile(member)
    with pytest.raises(ValueError, match="non-regular"):
        module.extract_suite(sdist, tmp_path / "bad-link", pins)


def test_release_metadata_and_document_links():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = json.loads((ROOT / "docs/release-pins.json").read_text())
    check_pin_identity(pins, metadata)
    assert pins["tag"] == "v" + pins["version"]
    assert (
        pins["wheel"]
        == pins["distribution"].replace("-", "_") + "-" + pins["version"] + "-py3-none-any.whl"
    )
    assert (
        pins["sdist"] == pins["distribution"].replace("-", "_") + "-" + pins["version"] + ".tar.gz"
    )
    for document in [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *(ROOT / "docs").glob("*.md")]:
        prose = re.sub(r"```.*?```|`[^`]*`", "", document.read_text(encoding="utf-8"), flags=re.S)
        for target in re.findall(r"\]\(([^)\s]+)\)", prose):
            if "://" not in target and not target.startswith("#"):
                assert (document.parent / target.split("#")[0]).exists(), (document, target)


def test_verified_manifest(tmp_path):
    pins = assets(tmp_path)
    module.verify_assets(tmp_path, pins)


@pytest.mark.parametrize("mutation", ["bytes", "missing", "extra", "manifest", "duplicate"])
def test_rejects_inconsistent_release(tmp_path, mutation):
    pins = assets(tmp_path)
    if mutation == "bytes":
        (tmp_path / "wheel.whl").write_bytes(b"tampered")
    elif mutation == "missing":
        (tmp_path / "wheel.whl").unlink()
    elif mutation == "extra":
        (tmp_path / "surprise.py").write_bytes(b"extra")
    else:
        manifest = tmp_path / "SHA256SUMS"
        manifest.write_text(manifest.read_text() * 2 if mutation == "duplicate" else "")
        # Even a correctly authenticated manifest must enumerate the exact payload set.
        pins["assets"]["SHA256SUMS"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        module.verify_assets(tmp_path, pins)


def test_asset_verification_survives_optimized_python(tmp_path):
    pins = assets(tmp_path)
    (tmp_path / "wheel.whl").write_bytes(b"tampered")
    code = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(ROOT / 'tools')!r}); "
        "from certify_release import verify_assets\n"
        f"try: verify_assets(Path({str(tmp_path)!r}), {pins!r})\n"
        "except ValueError: pass\n"
        "else: raise RuntimeError('optimized Python skipped digest verification')\n"
    )
    subprocess.run([sys.executable, "-O", "-c", code], check=True)


def test_interleaved_benchmark_order_and_regression_gate(tmp_path, monkeypatch):
    benchmark_spec = importlib.util.spec_from_file_location(
        "benchmark", ROOT / "tools/benchmark.py"
    )
    benchmark = importlib.util.module_from_spec(benchmark_spec)
    benchmark_spec.loader.exec_module(benchmark)
    calls = []

    def sample(argv, **kwargs):
        calls.append(argv[0])
        baseline = argv[0] == benchmark.sys.executable
        assert argv[argv.index("--tests") + 1] == ("historical-tests" if baseline else "head-tests")
        assert argv[argv.index("--package") + 1] == ("old_import" if baseline else "renamed_import")
        value = {
            "python": "3.13",
            "platform": "fixture",
            "method": "fixture",
            "measurements": {
                name: {"samples_seconds": [0.1], "median_seconds": 0.1}
                for name in benchmark.BUDGETS
            },
        }
        Path(argv[3]).write_text(json.dumps(value))

    monkeypatch.setattr(benchmark.subprocess, "run", sample)
    output = tmp_path / "candidate.json"
    benchmark.compare(
        Path("candidate-python"),
        output,
        package="old_import",
        tests=Path("historical-tests"),
        peer_package="renamed_import",
        peer_tests=Path("head-tests"),
    )
    assert calls == [
        benchmark.sys.executable,
        "candidate-python",
        "candidate-python",
        benchmark.sys.executable,
    ] * 2 + [benchmark.sys.executable, "candidate-python"]
    result = json.loads(output.read_text())
    assert len(result["measurements"]["generation"]["samples_seconds"]) == 5
    result["measurements"]["generation"]["median_seconds"] = 1.0
    with pytest.raises(AssertionError):
        benchmark.enforce(result)


def test_benchmark_canonicalizes_os_temp_alias(tmp_path, monkeypatch):
    benchmark_spec = importlib.util.spec_from_file_location(
        "benchmark", ROOT / "tools/benchmark.py"
    )
    benchmark = importlib.util.module_from_spec(benchmark_spec)
    benchmark_spec.loader.exec_module(benchmark)
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlinks")
    monkeypatch.setattr(benchmark.tempfile, "tempdir", str(alias))
    with benchmark.temporary_root() as root:
        assert root.parent == actual.resolve()
        assert root == root.resolve()
        assert root.is_dir()
    assert not root.exists()


def test_renaming_inventory_finds_paths_and_variants(tmp_path, monkeypatch):
    inventory_spec = importlib.util.spec_from_file_location(
        "inventory", ROOT / "tools/rename_inventory.py"
    )
    inventory = importlib.util.module_from_spec(inventory_spec)
    inventory_spec.loader.exec_module(inventory)
    (tmp_path / "scriptkit.py").write_text("SCRIPTKIT_ROOT = 'pokanop_scriptkit'\n")
    monkeypatch.setattr(inventory.subprocess, "check_output", lambda *a, **k: b"scriptkit.py\0")
    assert "| `scriptkit.py` | 1 |" in inventory.inventory(tmp_path)

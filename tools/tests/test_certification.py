"""Release evidence must fail closed on substitution or missing manifest entries."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tomllib

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


def test_release_metadata_and_document_links():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = json.loads((ROOT / "docs/release-pins.json").read_text())
    assert pins["version"] == metadata["version"]
    assert pins["tag"] == "v" + pins["version"]
    assert (
        pins["wheel"]
        == metadata["name"].replace("-", "_") + "-" + pins["version"] + "-py3-none-any.whl"
    )
    assert pins["sdist"] == metadata["name"].replace("-", "_") + "-" + pins["version"] + ".tar.gz"
    assert metadata["urls"]["Repository"] == "https://github.com/" + pins["repository"]
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
    with pytest.raises(AssertionError):
        module.verify_assets(tmp_path, pins)


def test_interleaved_benchmark_order_and_regression_gate(tmp_path, monkeypatch):
    benchmark_spec = importlib.util.spec_from_file_location(
        "benchmark", ROOT / "tools/benchmark.py"
    )
    benchmark = importlib.util.module_from_spec(benchmark_spec)
    benchmark_spec.loader.exec_module(benchmark)
    calls = []

    def sample(argv, **kwargs):
        calls.append(argv[0])
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
    benchmark.compare(Path("candidate-python"), output)
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

"""Certify pinned GitHub artifacts and the original consumer outside both checkouts.

Requires Python 3.11+, git, gh (authenticated for attestations) and build tooling.
Never publishes, changes trust settings, or uses PyPI to acquire the framework.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import tomllib
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def verify_assets(directory, pins):
    """Exact manifest membership and bytes; authenticated pins are reviewed in git."""
    expected = pins["assets"]
    if {p.name for p in directory.iterdir()} != set(expected):
        raise ValueError("unexpected/missing asset")
    for name, digest in expected.items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError("asset digest mismatch: " + name)
    manifest = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        if name in manifest:
            raise ValueError("duplicate checksum entry")
        manifest[name] = digest
    if manifest != {k: v for k, v in expected.items() if k not in ("SHA256SUMS", "build.json")}:
        raise ValueError("checksum manifest membership mismatch")


def extract_suite(sdist, destination, pins):
    """Extract only authenticated regular sdist contents, never links/devices."""
    with tarfile.open(sdist) as archive:
        if any(not (member.isfile() or member.isdir()) for member in archive.getmembers()):
            raise ValueError("non-regular sdist member")
        archive.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("expected one sdist root")
    root = roots[0]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if metadata["name"] != pins["distribution"] or metadata["version"] != pins["version"]:
        raise ValueError("historical sdist identity mismatch")
    if metadata["scripts"].get(pins["command"]) != pins["module"] + ".entrypoint:main":
        raise ValueError("historical sdist entrypoint mismatch")
    return root


def package_payload(wheel, module):
    with zipfile.ZipFile(wheel) as archive:
        return {
            name: archive.read(name) for name in archive.namelist() if name.startswith(module + "/")
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    pins = json.loads((ROOT / "docs/release-pins.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    candidate_module = next(iter(project["scripts"].values())).split(".")[0]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env.update(PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1", NO_COLOR="1")
    report = {"pins": pins, "steps": [], "complete": False}
    try:
        with tempfile.TemporaryDirectory(prefix="scriptkit-certification-") as temporary:
            work = Path(temporary)
            assets = work / "assets"
            run(
                "gh",
                "release",
                "download",
                pins["tag"],
                "--repo",
                pins["repository"],
                "--dir",
                assets,
                cwd=work,
                env=env,
            )
            verify_assets(assets, pins)
            for name in pins["assets"]:
                run(
                    "gh",
                    "attestation",
                    "verify",
                    assets / name,
                    "--repo",
                    pins["repository"],
                    "--signer-workflow",
                    pins["repository"] + "/.github/workflows/release.yml",
                    "--source-digest",
                    pins["commit"],
                    "--signer-digest",
                    pins["commit"],
                    "--source-ref",
                    "refs/tags/" + pins["tag"],
                    "--deny-self-hosted-runners",
                    cwd=work,
                    env=env,
                )
            report["steps"].append("all seven assets: hashes, exact manifest, pinned attestations")
            suite = extract_suite(assets / pins["sdist"], work / "historical-suite", pins)
            report["suite_source"] = pins["sdist"]
            wheel = assets / pins["wheel"]
            manager_root = work / "manager café"
            bootstrap = [
                sys.executable,
                "-I",
                assets / "bootstrap.py",
                "--root",
                manager_root,
                "--wheel",
                "https://github.com/"
                + pins["repository"]
                + "/releases/download/"
                + pins["tag"]
                + "/"
                + pins["wheel"],
                "--sha256",
                pins["assets"][pins["wheel"]],
                "--version",
                pins["version"],
            ]
            run(*bootstrap, cwd=work, env=env)
            launcher = (
                manager_root
                / "bin"
                / (pins["command"] + ".cmd" if os.name == "nt" else pins["command"])
            )
            run(launcher, "--json", "doctor", cwd=work, env=env)
            run(*bootstrap, cwd=work, env=env)
            run(launcher, "self-rollback", cwd=work, env=env)
            report["steps"].append(
                "documented public-HTTPS bootstrap twice, doctor and manager rollback"
            )
            run(
                sys.executable,
                ROOT / "tools/check_artifact.py",
                wheel,
                "--suite-root",
                suite,
                "--results",
                output / "framework.xml",
                cwd=ROOT,
                env=env,
            )
            report["steps"].append("installed release wheel: full suite outside checkout")
            candidate = work / "candidate-dist"
            run(
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                candidate,
                ROOT,
                cwd=work,
                env=env,
            )
            candidate_env = work / "candidate-env"
            venv.EnvBuilder(with_pip=True).create(candidate_env)
            candidate_python = candidate_env / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            run(
                candidate_python,
                "-m",
                "pip",
                "install",
                next(candidate.glob("*.whl")),
                "pytest==9.1.1",
                cwd=work,
                env=env,
            )
            baseline_env = work / "baseline-env"
            venv.EnvBuilder(with_pip=True).create(baseline_env)
            baseline_python = baseline_env / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            run(baseline_python, "-m", "pip", "install", wheel, "pytest==9.1.1", cwd=work, env=env)
            shutil.copy(ROOT / "tools/benchmark.py", work / "benchmark.py")
            run(
                baseline_python,
                "-I",
                work / "benchmark.py",
                output / "candidate-benchmark.json",
                "--peer-python",
                candidate_python,
                "--package",
                pins["module"],
                "--tests",
                suite / "tests",
                "--peer-package",
                candidate_module,
                "--peer-tests",
                ROOT / "tests",
                cwd=work,
                env=env,
            )
            report["steps"].append(
                "candidate performance compared against verified release on same runner"
            )
            rebuilt = work / "rebuilt"
            run(
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                rebuilt,
                assets / pins["sdist"],
                cwd=work,
                env=env,
            )
            rebuilt_wheel = next(rebuilt.glob("*.whl"))
            if package_payload(rebuilt_wheel, pins["module"]) != package_payload(
                wheel, pins["module"]
            ):
                raise ValueError("sdist payload drift")
            clean = work / "sdist-env"
            venv.EnvBuilder(with_pip=True).create(clean)
            python = clean / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run(python, "-m", "pip", "install", "--no-index", rebuilt_wheel, cwd=work, env=env)
            run(python, "-I", "-m", pins["module"], "--help", cwd=work, env=env)
            run(
                python,
                "-I",
                "-c",
                "from pathlib import Path; import importlib,sys; "
                f"runtime = importlib.import_module({pins['module']!r}); "
                "assert Path(runtime.__file__).is_relative_to(sys.prefix); "
                f"assert runtime.__version__ == {pins['version']!r}; "
                f"contracts = importlib.import_module({pins['module'] + '.contracts'!r}); "
                "assert contracts.resource_text('ToolSpec.example.json')",
                cwd=work,
                env=env,
            )
            report["steps"].append(
                "sdist rebuilt/installed outside checkout; all package resources match wheel"
            )
            consumer = work / "scripts"
            run("git", "clone", "https://github.com/pokanop/scripts", consumer, cwd=work, env=env)
            run("git", "checkout", "--detach", pins["scripts_commit"], cwd=consumer, env=env)
            run(
                python,
                "-m",
                "pip",
                "install",
                "pytest==9.1.1",
                "build==1.6.1",
                "requests==2.32.5",
                "rich==13.9.4",
                cwd=work,
                env=env,
            )
            run(
                python,
                "-m",
                "pytest",
                "-q",
                "tests/test_manager_migration.py",
                "tests/test_manager_legacy.py",
                "tests/test_manager_recovery.py",
                "tests/test_manager_review.py",
                "--junitxml=" + str(output / "consumer.xml"),
                cwd=consumer,
                env=env,
            )
            run(python, "tests/rehearse_generated_tool.py", cwd=consumer, env=env)
            run(python, "tests/rehearse_runtime_migration.py", cwd=consumer, env=env)
            report["steps"].append(
                "pinned consumer: migration/faults/rollback, generated registration and real legacy rehearsal"
            )
            report["complete"] = True
    finally:
        (output / "certification.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()

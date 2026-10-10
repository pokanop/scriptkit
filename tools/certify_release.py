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
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def verify_assets(directory, pins):
    """Exact manifest membership and bytes; authenticated pins are reviewed in git."""
    expected = pins["assets"]
    assert {p.name for p in directory.iterdir()} == set(expected), "unexpected/missing asset"
    for name, digest in expected.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, name
    manifest = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        assert name not in manifest, "duplicate checksum entry"
        manifest[name] = digest
    assert manifest == {k: v for k, v in expected.items() if k not in ("SHA256SUMS", "build.json")}


def package_payload(wheel):
    with zipfile.ZipFile(wheel) as archive:
        return {
            name: archive.read(name) for name in archive.namelist() if name.startswith("scriptkit/")
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    pins = json.loads((ROOT / "docs/release-pins.json").read_text())
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
            launcher = manager_root / "bin" / ("scriptkit.cmd" if os.name == "nt" else "scriptkit")
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
                "--benchmark",
                output / "benchmark.json",
                "--results",
                output / "framework.xml",
                cwd=ROOT,
                env=env,
            )
            report["steps"].append(
                "installed release wheel: full suite and benchmarks outside checkout"
            )
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
            shutil.copytree(ROOT / "tests", work / "tests")
            shutil.copy(ROOT / "tools/benchmark.py", work / "benchmark.py")
            run(
                candidate_python,
                "-I",
                work / "benchmark.py",
                output / "candidate-benchmark.json",
                "--baseline",
                output / "benchmark.json",
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
            assert package_payload(rebuilt_wheel) == package_payload(wheel), "sdist payload drift"
            clean = work / "sdist-env"
            venv.EnvBuilder(with_pip=True).create(clean)
            python = clean / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run(python, "-m", "pip", "install", "--no-index", rebuilt_wheel, cwd=work, env=env)
            run(python, "-I", "-m", "scriptkit", "--help", cwd=work, env=env)
            run(
                python,
                "-I",
                "-c",
                "from pathlib import Path; import scriptkit,sys; "
                "assert Path(scriptkit.__file__).is_relative_to(sys.prefix); "
                f"assert scriptkit.__version__ == {pins['version']!r}; "
                "from scriptkit.contracts import resource_text; "
                "assert resource_text('ToolSpec.example.json')",
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

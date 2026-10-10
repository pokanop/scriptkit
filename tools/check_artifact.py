"""Verify a wheel in a clean venv outside the checkout (Python 3.11+).

Run once bare and once with --rich. Only the Rich run installs the optional extra.
The library tests are copied, never run through a source-tree import shortcut.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import venv

from verify_results import verify


SMOKE = r"""
import importlib.metadata as metadata
import importlib.util
from pathlib import Path
import sys
sk = importlib.import_module(sys.argv[2])

assert Path(sk.__file__).is_relative_to(Path(sys.prefix)), sk.__file__
assert metadata.version(sys.argv[3]) == sk.__version__ == sys.argv[4]
assert importlib.util.find_spec("requests") is None
assert sk.HAS_RICH == (sys.argv[1] == "rich")
assert list(sk.track([1, 2])) == [1, 2]
assert sk.run([sys.executable, "-c", "print('wheel')"]).out == "wheel"
assert sk.run(["scriptkit-deliberately-missing-executable"]).code == -127
assert sk.run_cli(lambda: None) == 0
assert sk.run_cli(lambda: True) == 0
assert sk.run_cli(lambda: 7) == 7
assert sk.Config("missing.json", defaults={"answer": 42}).load().get("answer") == 42
sk.table(["name"], [["wheel"]])
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--rich", action="store_true")
    parser.add_argument(
        "--suite-root", type=Path, help="Verified historical sdist suite and metadata"
    )
    parser.add_argument("--benchmark", type=Path, help="Write installed benchmark JSON")
    parser.add_argument("--results", type=Path, help="Retain installed-suite JUnit evidence")
    parser.add_argument("--rich-version", help="Test a specific supported Rich version")
    args = parser.parse_args()
    if args.rich_version and not args.rich:
        parser.error("--rich-version requires --rich")
    wheel = args.wheel.resolve(strict=True)
    tooling = Path(__file__).resolve().parent
    root = args.suite_root.resolve(strict=True) if args.suite_root else tooling.parent
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    cli, entrypoint = next(iter(project["scripts"].items()))
    module = entrypoint.split(".")[0]
    with tempfile.TemporaryDirectory(prefix="scriptkit-artifact-") as tmp:
        work = Path(tmp)
        envdir = work / "venv"
        venv.EnvBuilder(with_pip=True).create(envdir)
        bindir = envdir / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        command = bindir / (cli + ".exe" if os.name == "nt" else cli)
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env["PYTHONUTF8"] = "1"
        env[module.upper() + "_TEST_WHEEL"] = str(wheel)

        def run(*argv: str) -> None:
            subprocess.run(argv, cwd=work, env=env, check=True)

        if args.rich:
            constraints = [f"rich=={args.rich_version}"] if args.rich_version else []
            run(str(python), "-m", "pip", "install", f"{wheel}[rich]", *constraints)
        else:
            run(str(python), "-m", "pip", "install", "--no-deps", str(wheel))
        run(
            str(python),
            "-I",
            "-c",
            SMOKE,
            "rich" if args.rich else "bare",
            module,
            project["name"],
            project["version"],
        )
        for launch in ([str(command)], [str(python), "-I", "-m", module]):
            run(*launch)
            run(*launch, "--help")
            run(*launch, "--version")
            result = subprocess.run([*launch, "install"], cwd=work, env=env, capture_output=True)
            assert result.returncode == 2, result
        for source, code in [
            ("def main(): return 7", 7),
            ("def main(): raise sk.CliError('expected')", 1),
            ("def main(): raise KeyboardInterrupt", 130),
        ]:
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    f"import importlib; sk = importlib.import_module({module!r})\n"
                    + source
                    + "\nraise SystemExit(sk.run_cli(main))",
                ],
                cwd=work,
                env=env,
                capture_output=True,
            )
            assert result.returncode == code, result
        run(
            str(python),
            "-m",
            "pip",
            "install",
            "pytest==9.1.1",
            "jsonschema==4.26.0",
            "build==1.6.1",
        )
        shutil.copytree(root / "tests", work / "tests")
        shutil.copytree(root / "examples", work / "examples")
        for installer in ("install.sh", "install.ps1"):
            shutil.copy(root / installer, work / installer)
        run(str(python), "-I", "-m", "pytest", "-q", "tests", "--junitxml=results.xml")
        verify(work / "results.xml")
        if args.results:
            shutil.copy(work / "results.xml", args.results.resolve())
        if args.benchmark:
            shutil.copy(tooling / "benchmark.py", work / "benchmark.py")
            run(
                str(python),
                "-I",
                str(work / "benchmark.py"),
                str(args.benchmark.resolve()),
                "--package",
                module,
                "--tests",
                str(work / "tests"),
            )
        print(f"Installed wheel verified ({'rich' if args.rich else 'bare'}): {wheel.name}")


if __name__ == "__main__":
    main()

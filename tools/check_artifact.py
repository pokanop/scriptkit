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
import sys
import tempfile
import venv


SMOKE = r'''
import importlib.metadata as metadata
import importlib.util
from pathlib import Path
import sys
import scriptkit as sk

assert Path(sk.__file__).is_relative_to(Path(sys.prefix)), sk.__file__
assert metadata.version("pokanop-scriptkit") == sk.__version__ == "1.3.0"
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
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--rich", action="store_true")
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="scriptkit-artifact-") as tmp:
        work = Path(tmp)
        envdir = work / "venv"
        venv.EnvBuilder(with_pip=True).create(envdir)
        bindir = envdir / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        command = bindir / ("scriptkit.exe" if os.name == "nt" else "scriptkit")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env["PYTHONUTF8"] = "1"

        def run(*argv: str) -> None:
            subprocess.run(argv, cwd=work, env=env, check=True)

        if args.rich:
            run(str(python), "-m", "pip", "install", f"{wheel}[rich]")
        else:
            run(str(python), "-m", "pip", "install", "--no-deps", str(wheel))
        run(str(python), "-I", "-c", SMOKE, "rich" if args.rich else "bare")
        for launch in ([str(command)], [str(python), "-I", "-m", "scriptkit"]):
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
                [str(python), "-I", "-c", "import scriptkit as sk\n" + source + "\nraise SystemExit(sk.run_cli(main))"],
                cwd=work, env=env, capture_output=True,
            )
            assert result.returncode == code, result
        run(str(python), "-m", "pip", "install", "pytest>=8")
        shutil.copytree(root / "tests", work / "tests")
        run(str(python), "-I", "-m", "pytest", "-q", "tests")
        print(f"Installed wheel verified ({'rich' if args.rich else 'bare'}): {wheel.name}")


if __name__ == "__main__":
    main()

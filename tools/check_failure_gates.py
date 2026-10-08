"""Exercise pytest/mypy through the actual project config with broken inputs."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


project = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    shutil.copy2(project / "pyproject.toml", root / "pyproject.toml")
    shutil.copytree(project / "src", root / "src")
    (root / "tests").mkdir()
    (root / "tests" / "test_failure.py").write_text("def test_failure():\n    assert False\n")
    (root / "src" / "scriptkit" / "gate_probe.py").write_text(
        'value: int = "not an integer"\ndef untyped(value):\n    return value\n'
    )
    # No file-selection or strictness overrides: discovery and policy come from
    # pyproject.toml, just as in CI. Disabled discovery/strictness must fail this probe.
    for command, markers in [
        (["pytest", "--cov", "--cov-report=xml"], ["1 failed"]),
        (["mypy"], ["[assignment]", "[no-untyped-def]"]),
    ]:
        result = subprocess.run(
            [sys.executable, "-m", *command], cwd=root, capture_output=True, text=True
        )
        if result.returncode != 1 or not all(marker in result.stdout for marker in markers):
            raise RuntimeError(f"Configured gate did not reject fixture: {result}")
        print(f"Configured {command[0]} correctly rejected intentional failure")

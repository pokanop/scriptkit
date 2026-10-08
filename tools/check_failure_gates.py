"""Exercise the actual pytest/mypy binaries with deliberately broken inputs."""

import subprocess
import sys
import tempfile
from pathlib import Path


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "test_failure.py").write_text("def test_failure():\n    assert False\n")
    (root / "bad_type.py").write_text('value: int = "not an integer"\n')
    for command, marker in [
        (["pytest", "-q", "test_failure.py"], "1 failed"),
        (["mypy", "--strict", "bad_type.py"], "[assignment]"),
    ]:
        result = subprocess.run(
            [sys.executable, "-m", *command], cwd=root, capture_output=True, text=True
        )
        if result.returncode != 1 or marker not in result.stdout:
            raise RuntimeError(f"Gate did not reject fixture: {result}")
        print(f"{command[0]} correctly rejected intentional failure")

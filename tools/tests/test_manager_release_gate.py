"""Execute the publication guard, not just a text assertion about its intent."""

import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "version, tag, accepted",
    [
        ("1.3.0", "v1.3.0", False),
        ("1.4.0", "v1.4.0", True),
        ("1.4.0", "v1.3.0", False),
    ],
)
def test_manager_release_requires_fresh_version(tmp_path, version, tag, accepted):
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
    line = next(
        line.strip()
        for line in workflow.read_text().splitlines()
        if line.strip().startswith("python -c ") and "tomllib" in line
    )
    code = shlex.split(line)[2]
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**os.environ, "TAG": tag},
        capture_output=True,
    )
    assert (result.returncode == 0) == accepted, result.stderr

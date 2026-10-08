"""Opt-in execution of packaged reference fixtures, NOT arbitrary project code."""

from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
import sys
import tempfile

from scriptkit.execution import BoundedRunner


def check_fixtures(
    *, allow_execution: bool, runner: BoundedRunner | None = None
) -> dict[str, object]:
    if not allow_execution:
        raise ValueError(
            "Runtime fixtures require --allow-execution; static checks execute no project code"
        )
    runner = runner or BoundedRunner()
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="scriptkit-conformance-") as directory:
        root = Path(directory)
        fixture = root / "example.py"
        fixture.write_bytes(
            files("scriptkit.conformance.resources").joinpath("example.py").read_bytes()
        )
        for flag, expected in (("", 0), ("--fail", 1), ("--cancel", 130)):
            result = runner.run(
                [sys.executable, "-I", str(fixture), "--json", *([flag] if flag else [])],
                cwd=root,
                timeout=10,
                max_output=65536,
            )
            try:
                envelope = json.loads(result.out)
                valid = (
                    isinstance(envelope, dict)
                    and set(envelope) == {"schema_version", "ok", "data", "error"}
                    and envelope["schema_version"] == 1
                    and envelope["ok"] is (expected == 0)
                    and result.code == expected
                    and (expected != 0 or not result.err)
                )
            except ValueError:
                valid = False
            results.append({"case": flag or "success", "valid": valid, "exit_code": result.code})
    return {
        "mode": "packaged-runtime-fixtures",
        "valid": all(r["valid"] for r in results),
        "cases": results,
        "scope": "Disposable reference fixtures only; project code was not executed or certified safe.",
    }

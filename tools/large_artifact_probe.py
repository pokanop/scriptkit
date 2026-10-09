"""Run the shared large-artifact integration probe from a source checkout."""

from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tests" / "large_artifact_probe.py"),
        run_name="__main__",
    )

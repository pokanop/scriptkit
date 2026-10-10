"""Installed-artifact benchmark worker; run from the disposable acceptance directory."""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


def measure(action, repeats=5):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        action()
        samples.append(time.perf_counter() - start)
    return {"samples_seconds": samples, "median_seconds": statistics.median(samples)}


def main():
    from scriptkit import __version__
    from scriptkit.contracts import ToolSpec, resource_text
    from scriptkit.generator import apply, preview
    from scriptkit.generator.scaffolds import tool
    from scriptkit.manager import PipBackend

    # Reuse the same audited, digest-pinned wheel fixture as installer acceptance.
    sys.path.insert(0, str(Path.cwd() / "tests"))
    from test_installer import fixture

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    spec = json.loads(resource_text("ToolSpec.example.json"))
    spec["entrypoint"] = "demo.cli:main"
    spec = ToolSpec.from_dict(spec)

    def generate():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply(root, preview(root, tool(spec, "standalone")))

    def install():
        with tempfile.TemporaryDirectory() as tmp:
            manager, plan = fixture(Path(tmp), wheel=True, backend=PipBackend())
            manager.install(plan)

    def spawn(*arguments):
        subprocess.run([sys.executable, "-I", *arguments], check=True, capture_output=True)

    measurements = {
        "interpreter": measure(lambda: spawn("-c", "pass")),
        "import": measure(lambda: spawn("-c", "import scriptkit")),
        "help": measure(lambda: spawn("-m", "scriptkit", "--help")),
        "generation": measure(generate),
        "installer": measure(install, repeats=3),
    }
    # Absolute safety ceilings, not performance claims; platform baselines are retained.
    budgets = {"interpreter": 1, "import": 1, "help": 2, "generation": 2, "installer": 60}
    report = {
        "schema_version": 1,
        "framework": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "method": "fresh process; warm filesystem; no network; median; wall clock",
        "measurements": measurements,
        "budgets_seconds": budgets,
        "installer_includes": "venv + ensurepip + verified tiny wheel + smoke + receipt",
    }
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        assert baseline["python"] == report["python"]
        assert baseline["platform"] == report["platform"]
        report["baseline"] = baseline
        report["relative_budgets_seconds"] = {
            name: 2 * result["median_seconds"] + (2 if name == "installer" else 0.1)
            for name, result in baseline["measurements"].items()
        }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, result in measurements.items():
        assert result["median_seconds"] < budgets[name], (name, result, budgets[name])
        if args.baseline:
            limit = report["relative_budgets_seconds"][name]
            assert result["median_seconds"] < limit, (name, result, limit)


if __name__ == "__main__":
    main()

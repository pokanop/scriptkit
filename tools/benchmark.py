"""Installed-artifact benchmark worker with interleaved baseline/candidate sampling."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

BUDGETS = {"interpreter": 1, "import": 1, "help": 2, "generation": 2, "installer": 60}


@contextmanager
def temporary_root():
    # macOS /var aliases /private/var. Canonicalize our own newly-created root;
    # do not weaken the installer's symlink rejection for untrusted inner state.
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp).resolve()


def measure(action, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        action()
        samples.append(time.perf_counter() - start)
    return {"samples_seconds": samples, "median_seconds": statistics.median(samples)}


def collect(single=False):
    from scriptkit import __version__
    from scriptkit.contracts import ToolSpec, resource_text
    from scriptkit.generator import apply, preview
    from scriptkit.generator.scaffolds import tool
    from scriptkit.manager import PipBackend

    sys.path.insert(0, str(Path.cwd() / "tests"))
    from test_installer import fixture

    spec = json.loads(resource_text("ToolSpec.example.json"))
    spec["entrypoint"] = "demo.cli:main"
    spec = ToolSpec.from_dict(spec)

    def generate():
        with temporary_root() as root:
            apply(root, preview(root, tool(spec, "standalone")))

    def install():
        with temporary_root() as root:
            manager, plan = fixture(root, wheel=True, backend=PipBackend())
            manager.install(plan)

    def spawn(*arguments):
        subprocess.run([sys.executable, "-I", *arguments], check=True, capture_output=True)

    repeats = 1 if single else 5
    return {
        "schema_version": 1,
        "framework": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "method": "fresh process; warm filesystem; no network; median; wall clock",
        "measurements": {
            "interpreter": measure(lambda: spawn("-c", "pass"), repeats),
            "import": measure(lambda: spawn("-c", "import scriptkit"), repeats),
            "help": measure(lambda: spawn("-m", "scriptkit", "--help"), repeats),
            "generation": measure(generate, repeats),
            "installer": measure(install, 1 if single else 3),
        },
        "budgets_seconds": BUDGETS,
        "installer_includes": "venv + ensurepip + verified tiny wheel + smoke + receipt",
    }


def combine(samples):
    result = dict(samples[0])
    result["method"] += "; five interleaved paired rounds; alternating order"
    result["measurements"] = {}
    for name in BUDGETS:
        values = [v for sample in samples for v in sample["measurements"][name]["samples_seconds"]]
        result["measurements"][name] = {
            "samples_seconds": values,
            "median_seconds": statistics.median(values),
        }
    return result


def with_baseline(report, baseline):
    assert baseline["python"] == report["python"]
    assert baseline["platform"] == report["platform"]
    report["baseline"] = baseline
    report["relative_budgets_seconds"] = {
        name: 2 * result["median_seconds"] + (2 if name == "installer" else 0.1)
        for name, result in baseline["measurements"].items()
    }
    return report


def enforce(report):
    for name, result in report["measurements"].items():
        assert result["median_seconds"] < BUDGETS[name], (name, result, BUDGETS[name])
        if "relative_budgets_seconds" in report:
            limit = report["relative_budgets_seconds"][name]
            assert result["median_seconds"] < limit, (name, result, limit)


def save(path, report):
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def compare(peer_python, output):
    # Measure both environments after provisioning, interleaved rather than before
    # and after build/pip/AV activity. Fixed rounds, no retry-until-green policy.
    reports = {"baseline": [], "candidate": []}
    with temporary_root() as temporary:
        for index in range(5):
            order = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
            for kind in order:
                sample = temporary / f"{kind}-{index}.json"
                python = sys.executable if kind == "baseline" else str(peer_python)
                subprocess.run(
                    [python, "-I", str(Path(__file__).resolve()), str(sample), "--single-sample"],
                    check=True,
                )
                reports[kind].append(json.loads(sample.read_text(encoding="utf-8")))
    baseline = combine(reports["baseline"])
    candidate = with_baseline(combine(reports["candidate"]), baseline)
    save(output.with_name("benchmark.json"), baseline)
    save(output, candidate)
    enforce(baseline)
    enforce(candidate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--baseline", type=Path)
    mode.add_argument("--peer-python", type=Path)
    mode.add_argument("--single-sample", action="store_true")
    args = parser.parse_args()
    if args.peer_python:
        compare(args.peer_python, args.output)
        return
    report = collect(args.single_sample)
    if args.baseline:
        with_baseline(report, json.loads(args.baseline.read_text(encoding="utf-8")))
    save(args.output, report)
    if not args.single_sample:
        enforce(report)


if __name__ == "__main__":
    main()

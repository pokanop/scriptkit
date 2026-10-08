"""Regenerate bundled schema and golden examples from the public contracts."""

import json
from pathlib import Path

from scriptkit.contracts import (
    AIProposal,
    ArgumentSpec,
    Artifact,
    CatalogRelease,
    CommandSpec,
    DependencyLock,
    Generation,
    InstallPlan,
    Platform,
    PythonRequirement,
    Receipt,
    ToolRelease,
    ToolSpec,
)


def main():
    platform = Platform("linux", "x86_64")
    python = PythonRequirement("3.11.0", "3.15.0")
    argument = ArgumentSpec(
        1, "target", "positional", "string", True, None, (), "Target to inspect"
    )
    command = CommandSpec(1, "inspect", "Inspect local files", (argument,))
    tool = ToolSpec(
        1,
        "example-tool",
        "1.0.0",
        "An offline example",
        "example_tool:main",
        python,
        (platform,),
        (command,),
    )
    artifact = Artifact("tools/example-tool-1.0.0.zip", "a" * 64, 42)
    lock = DependencyLock(1, platform, python, "pip", ())
    release = ToolRelease(tool, artifact, (lock,))
    catalog = CatalogRelease(1, "local-catalog", "1.0.0", (release,))
    plan = InstallPlan(
        1,
        "generation-1",
        None,
        "b" * 64,
        release,
        platform,
        "3.11.9",
        ("pip",),
        "tools/example-tool",
    )
    receipt = Receipt(1, plan, 1700000000, (Artifact("example_tool.py", "a" * 64, 42),))
    generation = Generation(1, "generation-1", None, (receipt,))
    proposal = AIProposal(
        1, "request-1", "fake", "offline-v1", None, "Demonstrate validated data only", tool
    )
    root = Path(__file__).resolve().parents[1] / "src/scriptkit/contracts/resources"
    for value in (argument, command, tool, lock, catalog, plan, receipt, generation, proposal):
        cls = type(value)
        (root / f"{cls.__name__}.example.json").write_text(
            value.canonical_json() + "\n", encoding="utf-8"
        )
        (root / f"{cls.__name__}.schema.json").write_text(
            json.dumps(cls.json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert cls.from_json(value.canonical_json()) == value


if __name__ == "__main__":
    main()

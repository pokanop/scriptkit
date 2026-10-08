"""Resolve all remote workflow action pins as commits, not annotated tag objects.

Uses gh's authenticated GitHub API on CI (read-only github.token). API/network
errors fail closed. Local reusable workflows do not need a remote commit lookup.
The repository uses block-style YAML `uses:` entries, including quoted values.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(.*?)\s*$")
PIN = re.compile(r"([\w.-]+/[\w.-]+)(?:/[\w./-]+)?@([0-9a-f]{40})")


def action_pins(directory: Path) -> set[tuple[str, str]]:
    pins = set()
    for path in sorted(directory.iterdir()):
        if path.suffix not in (".yml", ".yaml"):
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = USES.fullmatch(line)
            if not match:
                continue
            value = match[1].split("#", 1)[0].strip().strip("\"'")
            if value.startswith("./"):
                continue
            pin = PIN.fullmatch(value)
            if pin is None:
                raise ValueError(f"{path}:{line_number}: expected a full commit pin: {value}")
            pins.add((pin[1], pin[2]))
    if not pins:
        raise ValueError("No remote action pins found")
    return pins


def verify_commit(repository: str, sha: str) -> None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/git/commits/{sha}"],
        check=True,
        capture_output=True,
        text=True,
    )
    if json.loads(result.stdout).get("sha") != sha:
        raise ValueError(f"Commit lookup mismatch: {repository}@{sha}")


def main() -> None:
    directory = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    for repository, sha in sorted(action_pins(directory)):
        verify_commit(repository, sha)
        print(f"Verified commit: {repository}@{sha}")


if __name__ == "__main__":
    main()

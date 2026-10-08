"""Narrow semantic exception to whole-file drift for authoring metadata."""

from __future__ import annotations

from copy import deepcopy
import re
import tomllib

from scriptkit.contracts import ToolSpec
from scriptkit.generator.render import render, sha256
from scriptkit.generator.scaffolds import tool


def metadata_extension(spec: ToolSpec, template: str, recorded_hash: str, current: bytes) -> bool:
    """Accept additive dependencies/ScriptKit settings only against a proven baseline.

    Generator reconciliation remains byte-owned and conflict-preserving. This is a
    validation policy, not permission for the generator to overwrite extensions.
    """
    try:
        rendered = tool(spec) if template == "2.0.0" else render(spec)
        baseline = rendered.generated["pyproject.toml"]
        if sha256(baseline) != recorded_hash:
            return False  # Cannot reconstruct this recorded generator version safely.
        expected = tomllib.loads(baseline.decode("utf-8"))
        actual = tomllib.loads(current.decode("utf-8"))
        candidate = deepcopy(actual)
        project = candidate.get("project")
        if not isinstance(project, dict):
            return False
        dependencies = project.get("dependencies", [])
        required = expected["project"].get("dependencies", [])
        if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
            return False
        if any(dependencies.count(d) != 1 for d in required):
            return False
        # Do not allow another requirement to override/conditionally shadow the runtime pin.
        names = [
            re.sub(r"[-_.]+", "-", re.split(r"[\s\[(<>=!~;@]", d)[0]).lower() for d in dependencies
        ]
        if names.count("pokanop-scriptkit") != sum("pokanop-scriptkit" in d for d in required):
            return False
        if "dependencies" in expected["project"]:
            project["dependencies"] = required
        else:
            project.pop("dependencies", None)
        settings = candidate.get("tool", {})
        if isinstance(settings, dict):
            if "scriptkit" in settings and not isinstance(settings["scriptkit"], dict):
                return False
            settings.pop("scriptkit", None)
            if not settings and "tool" not in expected:
                candidate.pop("tool", None)
        return candidate == expected
    except (ValueError, UnicodeError):
        return False

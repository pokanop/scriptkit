# Tool conformance and assistant resources (v1)

`scriptkit validate PROJECT` is read-only. `scriptkit --json validate PROJECT`
returns a v1 ScriptKit envelope containing a versioned report and sorted diagnostics
(code, project-relative path, line, message). Failure exits 1; cancellation exits 130.
No project imports, build hooks, installers, plugins or formatters run.

Supported authoring contract: src-layout ToolSpec tools, console script matching
metadata, scaffold_runtime.tool_main wiring, declared runtime imports and a JSON
object config.example.json. Both scaffold layouts and user-owned extensions work.
Handwritten tools can omit the ownership manifest; when present, all generated file
hashes and the spec hash are checked. This checks recorded ownership drift, not whether
a manifest was honestly authored or whether the latest templates were used.
Legacy generator-v1 tools should use template-upgrade to adopt doctor/config/output
conventions. Custom runtime implementations are intentionally not auto-certified.

| Code | Repair |
|---|---|
| SKV001 | Replace unsafe/unreadable paths with regular project files |
| SKV002 | Fix ToolSpec JSON |
| SKV003 | Fix pyproject metadata/TOML |
| SKV004 | Align name/version/Python range with spec |
| SKV005 | Align console script entrypoint |
| SKV006 | Declare dependencies / fix import-distribution mapping |
| SKV007 | Fix source syntax |
| SKV008 | Define the declared entrypoint function |
| SKV009 | Declare imported distribution (including optional imports) |
| SKV010 | Replace print with returned data or OutputContext |
| SKV011 | Wire the sanctioned doctor/config/output runtime |
| SKV012 | Supply a non-secret JSON object config example |
| SKV013 | Inspect generated drift and preview regeneration |
| SKV014 | Repair ownership manifest |
| SKV015 | Align literal embedded launcher spec with tool.json |

Import/distribution differences are explicit, never guessed from the host environment:

```toml
[tool.scriptkit.conformance.imports]
PIL = "Pillow"
```

Static checks are conservative syntax/convention checks, not semantic verification:
aliases, dynamic imports, reflective calls, monkeypatching and writes to streams can
escape detection. **A passing report never means arbitrary Python is safe.** Dependency
resolution, secret review, actual command behavior and custom runtime conformance need
separate review and tests. Filesystem races and hostile projects require OS isolation.

## Explicit runtime reference checks

`scriptkit validate PROJECT --runtime-fixtures --allow-execution` additionally copies
the packaged example into a disposable directory and executes it with bounded output,
timeout and process cleanup. Success, failure and cancellation envelopes are checked.
This verifies installed reference interfaces, **not project behavior**. It never executes
project commands. No consent means no execution. Temporary directories and isolated
Python mode are not security sandboxes. Project tests require separate explicit consent
and an appropriate disposable sandbox; do not use validation as an authorization gate.

## Installed authoring guidance

The wheel and sdist contain `scriptkit.conformance.resources`: `AGENTS.md`, `SKILL.md`,
and runnable `example.py`. Resource contract version is v1; no AI extras or provider
integration are required. Read/copy them with `importlib.resources.files`, as shown in
the skill. Use the rules in any assistant's standard instruction mechanism.

Run the example with `python -m scriptkit.conformance.resources.example --json`.
Omit `--json` for a table; use `--fail` or `--cancel` for failure/interruption. The recipe
separates invoice business logic from OutputContext/command.run, renders TableData,
and scopes progress cleanup. Scaffold handlers simply return data: do not nest run()
or emit extra envelopes inside them.

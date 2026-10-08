---
name: scriptkit-command-writing
version: 1
description: Provider-independent ScriptKit command authoring
---

## Write or extend a command

1. Read tool.json, project metadata, ownership manifest and AGENTS.md. Identify user-owned
   modules before editing. Never run project imports merely to discover commands.
2. Add declarative arguments through `scriptkit add-command` (preview first); implement
   dispatch in _handlers.run(command, values). values['config'] is a validated JSON object.
3. Put custom business logic in pure functions. Return structured data; raise meaningful
   errors. Do not print or swallow cancellation. The scaffold owns the output envelope.
4. For custom presentation outside scaffold handlers, use the complete `example.py`
   recipe packaged beside this skill. It demonstrates business logic, TableData,
   context.progress, command.run, machine output, failure and cancellation. Do not nest
   command.run inside a scaffold handler or produce a second JSON envelope.
5. Declare dependencies, update non-secret config.example.json, and test success,
   failure and interruption. Run static validate without project imports.
6. Ask for explicit execution consent before runtime tests, use disposable fixtures and
   appropriate OS sandboxing for untrusted code. Packaged runtime-fixtures only verify
   the reference API recipe. Inspect actual project behavior separately.

## Load installed resources

```python
from importlib.resources import files

resources = files("scriptkit.conformance.resources")
rules = resources.joinpath("AGENTS.md").read_text(encoding="utf-8")
recipe = resources.joinpath("example.py").read_text(encoding="utf-8")
```

Copy the rules/skill into your assistant's ordinary instructions directory. No provider
integration is required. Resource contract v1 is independent of tool spec/template versions.

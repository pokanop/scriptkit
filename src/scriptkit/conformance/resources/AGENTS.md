# ScriptKit authoring contract v1

These provider-independent rules apply to handwritten and assistant-authored tools.
Copy into your project's AGENTS.md or give them to any coding assistant; no AI service,
provider SDK, credentials, network, or special assistant integration is required.

- Keep ToolSpec tool.json, project metadata and console entrypoints consistent.
- Edit user-owned _handlers.py and modules; never edit hash-owned generated launchers.
  Validation permits additive pyproject dependencies and [tool.scriptkit.*] settings,
  while protecting all other generated metadata and the exact framework pin. Keep the
  manifest. Generator add-command/upgrade still conflicts on the extended file: preserve
  it, restore the recorded baseline for apply, then reapply only user additions to the
  new baseline and validate. Never delete the manifest or discard extensions.
  Preview `scriptkit add-command` / `template-upgrade` before applying changes.
- Use scaffold_runtime.tool_main for command parsing, read-only doctor/config, explicit
  dispatch, and the output boundary. Return JSON-compatible data from handlers.
  Return integer exit statuses only intentionally; unimplemented actions must fail.
- No implicit destructive action. Require explicit confirmation and validate input.
- Declare runtime dependencies in project.dependencies. Map differing import names in
  [tool.scriptkit.conformance.imports] (e.g. PIL = "Pillow"). Static import checks
  are conservative, including optional imports; dynamic imports need manual review.
- Keep business logic separate from presentation. Use OutputContext, TableData,
  context.progress and command.run, not print/Rich globals. One JSON envelope on stdout;
  diagnostics on stderr. Propagate KeyboardInterrupt so cancellation returns 130.
- Use safe_config / execution.BoundedRunner for sensitive IO/process operations.
  Avoid import-time effects. Never put secrets into fixtures, logs or example config.
- Run `scriptkit --json validate PROJECT`. Stable SKV codes identify repairs.
  Static validation NEVER proves Python safe or runtime behavior correct.
- Run project behavior tests in a disposable, suitably sandboxed environment after
  explicit consent. `validate PROJECT --runtime-fixtures --allow-execution` tests only
  packaged reference examples, NOT the project's code. A temporary directory is not
  a security sandbox. Never import or execute an untrusted project to validate it.

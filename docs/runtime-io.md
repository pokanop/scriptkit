# Opt-in runtime IO

These modules are additive. The 1.3.0 `Config`, `ManagedBlock`, `run`, exports,
coercion rules and result/signature snapshots remain unchanged. No directory is
created or migrated on import or path resolution. No runtime dependency was added.

## Strict configuration and atomic persistence

```python
from pathlib import Path
from scriptkit.safe_config import SafeConfig, ConfigError


def validate(data):
    if set(data) - {"port", "token"} or type(data.get("port")) is not int:
        raise ValueError("invalid schema")

cfg = SafeConfig(Path("config.json"), defaults={"port": 8080},
                 env_prefix="TOOL", coerce_env=True, validator=validate,
                 secret_fields=("token",))
effective = cfg.load()  # defaults < saved JSON < environment
cfg.save({"port": 9000, "token": {"source": "env", "name": "TOOL_TOKEN"}})
```

`SafeConfig` is the explicit opt-in. Malformed/truncated/non-object JSON, duplicate
keys and non-finite JSON numbers fail rather than silently resetting state.
OS/permission failures propagate. Application callbacks own unknown-key/type/schema
validation. They validate defaults + stored data (so invalid stored values cannot
be hidden by an override), then the final environment-merged data, and saved data.
Callback error messages are sanitized; cancellation propagates. Environment
coercion is the existing `coerce_scalar` behavior and is off by default.

`save` requires a successful `load`, compares exact bytes read, and persists only
its explicit argument, never environment overrides implicitly. On conflict reload,
reconcile intentionally, then retry. A second save by the same object is supported.
Snapshots/defaults are not shared mutable global state. Inject `environ` and the
small `StateIO` protocol for offline use/fault testing.

Declared secret fields accept only `{"source": "env" | "keyring", "name": "..."}`
(or null/missing); they never resolve values. Declare all application secret fields
and reject unknown fields in the validator. Environment overrides skip declared
secret paths (including ancestors and descendants) and the environment names used
by stored/default secret references. Thus setting `TOOL_TOKEN` in the example
never replaces the reference or copies its value into config. Non-secret overrides
such as `TOOL_PORT` work as before. This is not a heuristic secret scanner.
Provider adapters resolve references at use time. Never persist an effective config
containing credentials; never put secrets in managed regions/receipts. These
primitives emit no logs. `BoundedRunner(check=True)` also omits command arguments
and captured output from errors; captured output itself is the caller's responsibility.

### Persistence contract

`LocalStateIO.replace(path, data, expected=old_bytes_or_None, mode=0o600)`:

1. Acquire an exclusive sibling `.scriptkit-lock` (fail immediately if present).
2. Check target bytes under that lock; `None` means the target must not exist.
3. Write a unique private sibling, set permissions before publication, flush/fsync.
4. Atomically `os.replace` the target, then remove staging/lock files.

Pass `mode=None` to preserve an existing file's mode and POSIX owner/group under
the same lock. New files still default to `0600`. Ownership is applied before the
mode (chown may clear set-id bits), and failures abort before publishing. Explicit
integer modes retain the private-config behavior. `remove(path, expected=bytes)`
uses the same lock and byte comparison before unlinking; it never deletes stale or
foreign state. Custom `StateIO` adapters must implement both operations.

Write/chmod/chown/fsync/replace failures and Python interruption before replacement leave
the original intact. Concurrent cooperating writers cannot overwrite each other's
changes. If publication succeeds but cleanup is interrupted, reload to determine
the committed state. A killed process may leave a lock/staging file: verify no
writer is running and explicitly remove those remnants before retrying. Locks are
never stolen based on age. No automatic retries/last-writer-wins policy.

Targets cannot be symlinks. Parent directories must be trusted; this is not a
root-confined manager filesystem or a hostile-directory TOCTOU defense. Advisory
sidecar locks do not constrain external editors; an external write between the
comparison and replacement is outside this cooperative contract. Windows sharing
violations propagate without deleting the old target. POSIX modes are enforced;
Windows chmod is **not an ACL**, so provision a private parent ACL. Directory
fsync/power-loss durability and multi-file transactions are not promised.

## Platform paths

`resolve_paths("tool", platform=..., home=..., environ=..., overrides=...)` returns
`PlatformPaths(config, data, cache, state)` without touching disk:

- Linux/other: absolute XDG values or `.config`, `.local/share`, `.cache`, `.local/state`.
- macOS: `Library/Application Support` and `Library/Caches`.
- Windows: `APPDATA`/`LOCALAPPDATA` or `AppData/Roaming`/`AppData/Local`.
- Explicit per-kind absolute overrides win, including a caller's legacy location.

Platform/home/environment are injectable. Native path semantics are used; simulate
Windows path syntax on Windows (platform injection alone does not change Path's
host filesystem flavor). Relative environment roots are ignored; invalid explicit
overrides and unsafe application identifiers fail.

## Owned text regions / PATH edits

```python
from scriptkit.regions import OwnedRegion

region = OwnedRegion("tool.path")
receipt = region.apply(rc_path, 'export PATH="/opt/tool/bin:$PATH"')
receipt = region.apply(rc_path, 'export PATH="/opt/tool/bin:$PATH"', receipt=receipt)
region.clear(rc_path, receipt=receipt)
```

Persist the `RegionReceipt` owner/block/separator/created fields in the install receipt; the
caller must associate it with the correct file. Only the exact previously written
block may be updated or removed. Missing ownership, drift, duplicate/partial/
reversed fences and marker injection fail closed. Foreign marker substrings are
not fences. Repeated apply/clear is idempotent. Foreign bytes, CRLF and the original
EOF newline state survive cleanup; edits outside the block survive too. No backup
is overwritten or removed. New files use private `0600` mode; existing files retain
their mode and POSIX uid/gid on both apply and clear (using `mode=None`). A file
created by apply is removed on clear only if removing the owned region leaves it
empty. Existing empty files and created files with later foreign additions survive.
The `created` flag defaults to false for older receipts, conservatively retaining
the file when its creation is not proven. Windows ACL/owner and extended-attribute
preservation are not provided by chmod; callers needing those require a native
StateIO adapter. The helper only manages text: the caller owns platform-specific
shell escaping and PATH syntax.

## Bounded processes

```python
from scriptkit.execution import BoundedRunner
result = BoundedRunner(cancelled=stop_event.is_set).run(
    ["tool", "--version"], timeout=10, max_output=64 * 1024)
```

Only argument arrays, always `shell=False`; `cwd`, `env`, stdin, timeout and output
limits are explicit. Returns legacy `Result(code, out, err)`: nonzero exit is data,
missing executable is `-127`, timeout/OS failure/output overflow is `-1`.
`check=True` raises sanitized `CliError`. Cancellation and Ctrl-C re-raise
`KeyboardInterrupt` **after cleanup**, never return success. `ProcessRunner` is a
narrow injection boundary for consumers; clock/sleep/cancellation are injectable.

POSIX starts an isolated session and kills only that process group. Windows starts
suspended, assigns a kill-on-close Job Object, then resumes (no child-spawn race).
Failure to establish containment fails closed. Native Windows job integration and
child teardown tests run in the supported-platform CI matrix. Jobs also close on
normal parent exit, preventing leftover children. Deliberate POSIX session escape
requires an OS sandbox and is not covered by this cooperative runner.

Temporary-file output avoids pipe deadlocks and unbounded memory. Return capture
is byte-bounded; combined spool size is checked every 10ms, so disk consumption
can overshoot by one polling interval's production. The timeout is an execution
budget, not a hard real-time scheduling/OS-startup guarantee. Input and process
creation are synchronous. No global process-name or unrelated-PID termination.

## Verification

`tests/test_{state_io,safe_config,paths_regions,io_review,execution,windows_job}.py` cover
fault-injected writes/permissions/interruptions, concurrent conflicts, strict
loading, references, native/platform-injected paths, reversible ownership, process
limits, cancellation/Ctrl-C, descendant cleanup and unrelated-process survival.
The existing installed-wheel harness copies and runs these same tests outside the
checkout, both bare and with Rich, on Linux/macOS/Windows Python 3.11–3.14. WinAPI
failure paths also have portable fakes; fakes do not replace native CI evidence.

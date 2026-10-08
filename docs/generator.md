# Deterministic project generation

The opt-in `scriptkit.generator` layer consumes the version-1 `ToolSpec` contract.
It never imports the tool's modules, fetches templates, runs tasks, invokes an
external formatter or contacts a registry. Runtime imports remain unchanged.

```sh
mkdir my-project
python -m scriptkit.generator spec.json my-project            # JSON preview + unified diffs
python -m scriptkit.generator spec.json my-project --check    # no writes, including locks
python -m scriptkit.generator spec.json my-project --apply
python -m scriptkit.generator spec.json my-project --recover  # finish an interrupted apply
```

Check exits 0 for clean, 1 for drift, 2 for conflict/error. All operational results
are JSON. An interrupted transaction makes check return an error without recovery
or mutation. Apply emits the applied preview; a subsequent check is clean.
The root must already exist. The root itself and components below it must not be
symlinks/reparse points. Ancestors above the root are outside this boundary and
may be symlinks (for example macOS `/tmp` or a synced-folder alias). Input specs live
outside the generated `tool.json` unless you deliberately use that canonical copy.

## Ownership and architecture

- `render.py`: strict normalization, pinned wheel resource, deterministic rendering.
  Commands/platforms are sorted; positional argument order is preserved. Text is
  UTF-8/LF with fixed template whitespace (`text-lf-1`), not host formatter output.
- `plan.py`: read-only hash manifest validation, ownership and conflict diffs.
- `transaction.py`: injected `Writer` publication adapter, project lock and journal.
- `__main__.py`: small CLI composition layer; no manager/registry/AI dependencies.

Generated files are `tool.json`, `pyproject.toml`, the entrypoint module,
`.gitattributes`, and `.scriptkit-generator/.gitignore`. The attributes pin LF for
generated files and the manifest, including in `core.autocrlf=true` clones. The
control ignore file excludes locks, journals and staging, but keeps the manifest
and ignore file versioned. Commit these ownership/VCS files with the project.
Existing foreign `.gitattributes` files are not overwritten; generate in a new
project or explicitly reconcile your VCS policy before adopting generation.
The entrypoint must be `package.module:function` (nested packages supported);
Python keywords, `_handlers` and `__init__` entrypoint modules are refused, as are
function names that shadow launcher dependencies (`argparse`, `json`, `_handlers`,
`vars`, `int`, `str`, `__name__`).
The generated argparse launcher invokes one explicit `_handlers.run(command,
arguments)` function. It does not discover commands by importing modules.
Argument dictionary keys retain spec spelling, including hyphens.

Initializers and `_handlers.py` are **user owned**: created when absent and never
overwritten or removed. Other existing files are untouched. New generated paths
cannot silently adopt foreign files, even when bytes match. Generated files removed
by a new spec are deleted only if their saved hashes still match, or accepted as
already converged if already absent. Likewise an owned file already equal to the
proposal is converged, not a conflict. Foreign files are never adopted this way. Empty directories
are retained. Ownership transfers and overlapping paths fail closed.

`.scriptkit-generator/manifest.json` records generated SHA-256 hashes, user paths,
normalized spec hash, schema version, template version and formatter version.
Preview captures exact base bytes, including unchanged files. Apply rejects stale
bases before publishing; modified/missing generated files produce explicit conflict
records with `reason` (`modified`, `missing`, `foreign`) and current/proposed
unified diffs. An identical foreign file has an empty diff but a `foreign` reason. Resolve by preserving your handwritten
changes in user-owned files and restoring the generated base, not by deleting the
manifest to force adoption. There is no overwrite/force escape hatch.

## Interruption and safety boundary

Apply obtains a process-scoped nonblocking OS lock (flock on Unix, byte lock on
Windows). All generators for a project must cooperate with this lock. The lock
file is intentionally retained, but a dead process releases the actual OS lock.
A versioned journal containing before/after bytes is atomically persisted before
any project file is changed. Each file is atomically replaced; manifest is last.
`--recover` validates *all* journal paths/bytes, then idempotently rolls forward.
Recovery itself may be interrupted and retried. If a file matches neither before
nor after, recovery refuses and retains the journal for manual reconciliation.
It never overwrites that unexpected content or steals an active writer's lock.

### Reconciling a recovery conflict

`--recover` returns exit 2 with JSON `conflicts`: every conflicting path,
`before_absent`, `after_absent`, `current_absent`, and a current→after unified diff.
The Python API raises `RecoveryConflict` with the same dictionary in `.report`.
No project files are written when this preflight finds a conflict.

1. Stop other writers. Back up each conflicting file outside its generated path;
   preserve handwritten work in a user-owned handler or a separate file.
2. Choose whether to restore the exact before bytes or accept the exact after
   bytes from the journal. If `before_absent` is true, moving the conflicting file
   away restores the before state. If `after_absent` is true, moving it away
   accepts the planned deletion. Do not delete the journal or manifest.
3. For non-absent bytes, inspect/export the selected journal value without executing
   it. For example, this prints the proposed UTF-8 text (use binary `write_bytes`
   to a separate scratch file when exact non-text bytes are needed):

```python
import base64
import json
from pathlib import Path

journal = json.loads(Path(".scriptkit-generator/journal.json").read_text())
operation = next(op for op in journal["operations"] if op["path"] == "tool.json")
proposed = operation["after"]  # use "before" to restore the original baseline
if proposed is not None:
    Path("proposal-review.txt").write_bytes(base64.b64decode(proposed, validate=True))
```

4. After reviewing the exported bytes and backing up your changes, restore the
   chosen exact content at that one conflicting path. Repeat for all reported
   conflicts, then rerun `--recover`. It validates every path again and completes
   the same transaction; `--check` should then be clean. The journal is removed
   only after successful completion. Never edit hashes to conceal a conflict.

On permission/ownership errors (including POSIX `fchown` EPERM in shared projects),
restore the required access or ask the file owner to perform recovery. The engine
intentionally does not discard owner metadata or bypass permissions to proceed.

This guarantees recoverability after process interruption, **not simultaneous
multi-file visibility**: do not run a project with a pending journal. Readers do
not take generator locks. Journal and targets are fsynced before publication, but
directory fsync/power-loss durability is not promised. Parents/control state must
be trusted; this is not a sandbox against hostile concurrent filesystem changes.
Use one filesystem for the project. Windows modes are not ACLs. Existing POSIX
mode/owner are preserved, new files intentionally use private 0600 permissions,
including source files (not umask-derived). Owners may chmod sources for sharing;
subsequent applies preserve that mode. Arrange Windows directory ACLs.

## Template versions and migrations

The initial supported template is **1.0.0**, pinned by its SHA-256 in `render.py`;
resources are included in wheels. No mutable `latest`, remote path, Jinja code,
Copier extension, task or executable migration is accepted as input.
Manifest/journal schema v1 is strict; unknown fields/versions fail rather than
being guessed. Existing runtime projects without a manifest are not automatically
adopted. Repeated v1 spec updates are supported and tested.

There is no historical generator template to migrate in this first release.
The explicit migration table is currently identity-only (`1.0.0`/`text-lf-1`).
Any future template/formatter version requires a new pinned resource, a named
source→target data migration and backward-compatibility fixtures; changing the
resource/hash in place after release is forbidden. Until such a migration ships,
cross-version upgrades fail *before writes*, never execute hooks or claim files.

## Copier evaluation

Executable comparison (optional development dependency only):

```sh
uv run --no-project --with copier==9.9.1 python tools/evaluate_copier.py
```

Copier 9.9.1 passed deterministic-copy and untrusted-task-refusal probes. Its
direct overwrite rerun replaced an edited generated file, violating this engine's
ownership contract. Copier's richer update/merge workflow is not evidence of
hash-CAS + recoverable multi-file apply, and embedding it would still require this
entire ownership/transaction layer while adding a template execution surface.
We therefore selected the permitted simpler backend: fixed `string.Template`
substitution over bundled data. `tests/test_generator.py` is the engine's backend
contract suite (offline repeatability, handwritten preservation, diff conflicts,
stale bases, path safety, interruption/recovery, locking and installed resources).
No Copier dependency ships at runtime.

The release artifact suite runs those same tests outside checkout in bare and Rich
wheel environments. The existing CI matrix covers Linux/macOS/Windows and Python
3.11–3.14, including an actual child-process crash and subsequent recovery.

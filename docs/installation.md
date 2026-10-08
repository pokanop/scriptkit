# Transactional per-tool installation

`scriptkit.manager.Installer` is a reusable library service, not bootstrap/self-update
or a new CLI command. It consumes data-only `ResolvedPlan` records and injected
`InstallationSource` / `EnvironmentBackend` ports. The registry, runtime and AI do
not import the manager. Registry's historical record/validator imports remain
compatible aliases; their implementation now lives in `contracts` so the manager
need not depend on a registry provider.

```python
from pathlib import Path
from scriptkit.manager import Installer
from scriptkit.registry.source import RegistryArtifactSource

# resolver and resolved are supplied by the composition root (see registry.md).
source = RegistryArtifactSource(resolver, resolved.registry.namespace, offline=True)
manager = Installer(Path("private-state"), Path("private-bin"), source)
manager.install(resolved, dry_run=True)
receipt_path = manager.install(resolved)
# An update uses a new generation and previous_generation=current generation.
manager.rollback(resolved.installation.release.tool.name)
manager.uninstall(resolved.installation.release.tool.name)
```

Plans must target the running interpreter's exact version/platform, use the tool
name as destination, and select a never-before-used generation. A stale lineage
fails instead of overwriting another install. Bare launcher names cannot be taken
over by another registry namespace/origin while a tool is installed. The source
reauthorizes plans against current consent/pins/expiry before fetching; offline
policy belongs to that injected source. Dry-run validates without writing manager
state, creating environments or running package/tool code (the source may cache
metadata). It also checks lineage, registry ownership and generation availability
before any artifact fetch, using the same preflight as install. Pending journals
require explicit recovery before dry-run; it never commits recovery as a side
effect. Dry-run is a read-only snapshot, not a reservation against concurrent writers. Archive members, artifact bytes, dependency names/versions, and hashes
are checked before staging. Only wheels and Python-only legacy bundles are accepted.

## Phases and receipts

Plan → validate → fetch → stage → smoke → activate. `checkpoint(phase)` is an
injected fault/observation seam, including `journal` and `pointer` boundaries.
Smoke invokes the declared entrypoint with `--help` by default; applications may
supply explicit argument tuples. No catalog-provided install hooks run. Tool code
is trusted executable code: **a venv is not a sandbox**.

Each tool owns `generations/<generation>/`, with its environment, fetched artifacts,
legacy sources and fixed `run.py`. These paths are never relocated, avoiding
venv shebang relocation and Windows executable replacement. `receipt.json` is a
manager-private schema-version-1 envelope with the complete resolved plan (origin,
catalog digest, Python/platform, dependency locks and all artifact hashes), UTC
installation time, full relative file inventory (SHA-256 or symlink target), and
absolute launcher paths/digests. This envelope intentionally differs from the
portable distribution `contracts.Receipt`: native environments contain dotfiles
and interpreter links that distribution artifact inventory paths cannot express.
The receipt excludes itself. Rollback/recovery verifies the entire inventory;
modified/incomplete generations fail closed rather than being trusted. Rollback
deliberately does not reauthorize registry consent/pins/expiry: it restores an
already-local, previously accepted generation and remains usable offline. Removing
registry trust does not revoke existing local installations or rollback targets.

## Commit, recovery and ownership

A single kernel-held root lock serializes cooperating managers and is released on
process death; never remove `installer.lock`. A fsynced activation journal records
previous/target, followed by atomic replacement of the small `active.json` pointer.
Stable launchers consult this pointer on each invocation. Updates never replace a
launcher or running executable. `launchers.json` records the digests of accepted
existing bytes or bytes about to be published, before publication. Ownership accepts
these recorded bytes even when the host interpreter has an equivalent path spelling;
state/bin paths are normalized with `os.path.abspath`. Existing receipts provide
compatibility ownership evidence when no independent ledger exists. Missing files
are republished with the current launcher implementation and recorded anew.
POSIX launchers exec the generation interpreter, preserving its PID and signals. Recovery rolls a recorded activation **forward**;
failures before the journal leave the prior generation selected. Call
`manager.recover(tool_name)` after interruption; install/rollback/uninstall also
recover under the lock. A failure after pointer publication may mean the operation
committed: consult/recover state, do not infer rollback from an exception.

On Windows a file opened without delete sharing can prevent pointer replacement.
The operation preserves the old pointer and journal and can be retried once the
reader releases it; there is **no delete-then-rename fallback**. Launcher publication
uses an exclusive hard link to a completed staged file (NTFS/POSIX). Unsupported
filesystems fail closed. File contents are fsynced; directory durability across
power failure and remote/network filesystems are **not** guaranteed. These are
process-interruption guarantees, not an OS-independent power-loss claim.

All state/bin parents must be private trusted directories/ACLs. Symlink state,
foreign/edited launchers and modified generation inventories are refused. This
protects accidental collisions, not a hostile process sharing the same account.
Keep the manager interpreter at its original path: launchers bind to that host
interpreter. Moving/updating the host interpreter is bootstrap's separate concern.

Default uninstall removes owned launchers and deactivates the tool. It deliberately
retains environments/receipts (including failed stages) so running Windows readers
remain safe and audit/recovery evidence survives. It never deletes config or user
data. Destructive generation garbage collection is not exposed by this service;
operators may clean up private inactive generations after ensuring no readers.
Foreign files are never recursively removed. This retention trades disk space for
safety; failed staging consumes space until explicit administrative cleanup.

### Manual space reclamation

Stop tool processes and prevent new invocations first. Run recovery, then hold the
root's `scriptkit.manager.storage.locked(root)` lock while inspecting/deleting;
recheck that no tool journal exists after acquiring the lock. Never delete the
active generation, any journal target/previous generation, or generations in the
receipt's previous-generation chain that you still want available for rollback.
Only other inactive directories under that tool's `generations/` are candidates.
An unreferenced failed stage without a receipt is also a candidate once no staging
process remains. Inspect/backup unexpected or user-modified files before manually
removing a candidate; do not traverse symlinked roots or delete link targets. Leave
`installer.lock`, launchers/ownership ledger, pointers, config and user data alone.
Deletion is optional and irreversible; keeping receipts elsewhere preserves audit
evidence, not rollback capability. Automated pruning/retention is a proposed later
manager-maintenance scope, subject to PM confirmation—not part of bootstrap or
registry cache eviction.

## Backend decision and system dependencies

`PipBackend` is default: stdlib venv, bundled pip, no additional bootstrap download.
`UvBackend(Path(...))` is optional for an already provisioned uv executable. Both
use offline/no-index, no-deps wheel installation plus backend dependency checks;
uv explicitly uses `--link-mode=copy` so payload files do not share hardlinks with
its external cache. Both adapters accept a `timeout=` in seconds (default 120);
all transitive wheels must be in the verified plan, never implicitly resolved from
an index. Backend/tool processes run through the bounded process-tree runner.
Executable tests install a real local wheel, launch it, update and roll it back
using both adapters. Pip offers zero-extra-tool availability; uv is useful when
already provisioned but is not silently installed or selected. CI always exercises
pip; uv execution is conditional on host provisioning (command-policy tests always
run). Local POK-621 verification executed both adapters.

### Windows programmatic invocation

The `.cmd` shim is for **interactive use only**. Its `%*` argument forwarding has
the Windows batch/BatBadBut command-injection boundary: programs must not pass
untrusted arguments through it, even with `subprocess` and `shell=False`. Instead,
read the private tool's `active.json`, validate its target as a generation name,
and invoke that retained generation directly with an argument list:
`[str(generation / 'env/Scripts/python.exe'), '-I', '-B', str(generation / 'run.py'), *args]`.
Use `shell=False`; no command string or `.cmd` intermediary. Retained immutable
generations keep that snapshot usable across updates/uninstall. Any future pruning
API must coordinate these readers explicitly.

System package locks produce `doctor:` guidance and stop without staging. This
service never invokes sudo, apt, brew or winget. Provision prerequisites separately;
system package installation is not simulated by a Python environment.

Tests include local wheel and legacy bundle lifecycle, every phase fault,
subprocess hard exits, competing processes, ENOSPC/EACCES persistence failures,
foreign launchers, trust changes, locked-file retry, and a native Windows
non-delete-sharing pointer test. The normal installed-wheel suite runs these same
tests outside the checkout on the supported CI platform matrix.

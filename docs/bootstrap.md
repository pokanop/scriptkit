# Manager bootstrap, recovery and self-update

The bootstrap installs **only** the dependency-free ScriptKit manager. Tools are
explicit subsequent installs in separate environments. Python 3.11+ (including
`venv` and `ensurepip`) is a prerequisite; no system packages, sudo, registry
PATH values, shell profiles or execution policies are changed automatically.

## Release selection and verification

There is not yet a published **manager-capable** release. The existing
[`runtime-1.3.0-7c17061`](https://github.com/pokanop/scriptkit/releases/tag/runtime-1.3.0-7c17061)
is a real, immutable compatibility-runtime artifact, **not** a manager release;
do not use it for this workflow. The next reviewed release must publish the
rehearsal's `install.sh`, `install.ps1`, `bootstrap.py`, wheel, `SHA256SUMS` and
`build.json`. These are hashed by the release rehearsal and covered by the
release provenance workflow. No tag/publication of unreviewed code is performed
by bootstrap development. **The release owner must bump the distribution and
runtime version before publishing a manager release**; `1.3.0` is already used
by the runtime-only wheel and must not be reused. The publishing workflow rejects
that version; local compatibility development retains it until release preparation.

Obtain `RELEASE_URL` (the exact `/releases/download/<tag>` URL), `VERSION`,
`BOOTSTRAP_SHA256`, and `WHEEL_SHA256` from the verified release. Verify provenance
with `gh attestation verify` following [quality-and-release.md](quality-and-release.md).
A hash copied from an untrusted server is not independent authentication.
Mutable discovery such as GitHub's latest-release page is allowed only to select
these **exact** pins: the manager records the requested version, SHA-256, source
URL and generation in its receipt. There is no implicit latest/index fallback.
Even a mutable wheel URL must match the explicit pinned digest.

Pinned curl-to-shell (variables below are intentional release inputs, not a
claim that a manager release exists today):

```sh
curl --fail --silent --show-error --location "$RELEASE_URL/install.sh" |
  sh -s -- "$RELEASE_URL/bootstrap.py" "$BOOTSTRAP_SHA256" \
    --wheel "$RELEASE_URL/pokanop_scriptkit-$VERSION-py3-none-any.whl" \
    --sha256 "$WHEEL_SHA256" --version "$VERSION" --root "$HOME/ScriptKit café"
```

Curl-to-shell executes the shell wrapper before inspection. Prefer downloading
all three scripts and `SHA256SUMS`, checking their hashes against authenticated
release evidence, and inspecting before execution:

```sh
curl -fLO "$RELEASE_URL/install.sh"
curl -fLO "$RELEASE_URL/bootstrap.py"
# Compare sha256sum (Linux) / shasum -a 256 (macOS) with authenticated release hashes.
less install.sh bootstrap.py
python3 -I bootstrap.py --wheel "$RELEASE_URL/pokanop_scriptkit-$VERSION-py3-none-any.whl" \
  --sha256 "$WHEEL_SHA256" --version "$VERSION"
```

PowerShell (download/inspect `install.ps1` first; compare `Get-FileHash -Algorithm
SHA256` to the authenticated release hashes). Windows PowerShell **5.1** and
PowerShell **7.x** are supported, including legacy native argument passing.
Use a one-off process policy override for Windows' default `Restricted` policy;
it does not modify the machine/user execution policy (organization-enforced
Group Policy still takes precedence):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./install.ps1 `
  "$ReleaseUrl/bootstrap.py" $BootstrapSha256 `
  --wheel "$ReleaseUrl/pokanop_scriptkit-$Version-py3-none-any.whl" `
  --sha256 $WheelSha256 --version $Version --root "$HOME/ScriptKit café"
```

For PowerShell 7, substitute `pwsh` for `powershell.exe` in the same command.

Set `SCRIPTKIT_PYTHON` to the **path of an executable**, not a command with flags,
to select Python for either wrapper. Both wrappers verify the downloaded Python
bootstrap hash before running it and propagate its exit status. The standalone
bootstrap needs only the standard library and never imports the installed
runtime, including when repairing a broken installation.

## PATH and the public CLI

Default root: `~/.scriptkit`; commands are under `<root>/bin`. Bootstrap and CLI
canonicalize the selected root once, so symlinked homes and `/tmp` are supported;
symlinks redirecting state inside that canonical root remain forbidden. Invoke
`<root>/bin/scriptkit` (Unix) or `<root>/bin/scriptkit.cmd` (Windows) directly for
no-PATH operation. The stable launcher supplies its root to the CLI. When using
an unrelated Python's `python -m scriptkit`, use `--root` or `SCRIPTKIT_ROOT`.
Global flags precede subcommands. `--json` always emits a v1 output envelope on
stdout; phase messages/child output use stderr, without redirected animation.
Without `--json`, doctor prints readable key/value checks, registries and catalog
entries appear one per line, and lifecycle commands print short summaries rather
than internal plans or receipts.

Optional `--path-file <new-file>` creates an explicitly requested environment
file (`export PATH=...` on Unix, `$env:PATH=...` on Windows). Source/dot-source
that file yourself, or add the bin directory manually. Existing unrelated files
are refused. This is opt-in integration, not automatic profile modification.
Windows cmd launcher paths cannot contain `%`, `!`, quotes or newlines; spaces
and non-ASCII are supported. Windows wrappers temporarily select UTF-8 parsing
and restore the console code page afterward; manager Python uses explicit UTF-8
mode even with isolated `-I` startup. Like other `.cmd` launchers, these are not a secure
transport for untrusted shell metacharacters; use `python -m scriptkit` for that.

```sh
scriptkit --json doctor
scriptkit registry add registry.json --trust-origin https://tools.example.org/
scriptkit registry list
scriptkit catalog example
scriptkit install example/tool@1.2.3 --dry-run
scriptkit install example/tool@1.2.3
scriptkit update example/tool@1.2.4
scriptkit rollback tool
scriptkit recover tool
scriptkit uninstall tool
```

`registry.json` is the [pinned Registry contract](registry.md), not arbitrary
package-index access. Trust requires explicit origin consent. Offline mode uses
only reverified cached artifacts and does not waive expiry. Install and update
share the transactional installer; both accept exact versions, with stale
lineage rechecked under the tool-state lock. `scriptkit` is a reserved tool name.
Uninstall preserves data/config and generations. Tool dispatchers use the base
Python, not the manager venv, so repairing that venv cannot break tool commands.

## Update, interruption and recovery

```sh
scriptkit self-update --wheel "$WHEEL_URL" --sha256 "$WHEEL_SHA256" --version "$VERSION"
scriptkit self-rollback
# If scriptkit itself cannot start, use the independently verified bootstrap:
python3 -I bootstrap.py --root "$ROOT" --rollback
# If no previous healthy generation exists, rerun the pinned install command.
```

If the selected generation or interpreter is missing, the stable launcher exits 1
with a single recovery message rather than a traceback. With `--json` it also
emits the standard v1 failure envelope, without importing the broken runtime.
Missing/corrupt pointers and interpreter launch errors provide the same guidance.

Each attempt allocates a fresh manager generation, verifies wheel bytes and
package/version identity, creates a new venv, installs offline with `--no-deps`,
and smokes help/version plus the manager doctor command (rejecting runtime-only
releases). Only then is `manager-active.json` atomically replaced.
That single commit records both active and previous generations; there is no
partially committed multi-file activation journal to replay. An interrupted
attempt before the commit leaves the old manager selected; after the commit the
new complete manager is selected. Self-update never modifies its running venv.
Rollback smokes the previous generation before swapping pointers. Stable launchers
are published exclusively and never overwrite unrelated commands. A separate
ownership ledger records both current and intended launcher hashes before an
owned replacement, allowing repair after partial publication without accepting
foreign or user-modified files.

Rerunning is safe but allocates a fresh generation even for the same hash, so it
also repairs missing executables. Failed staging and old generations are retained:
there is deliberately no automatic garbage collection or deletion of potentially
running Windows environments. Plan disk capacity accordingly. The system/base
Python must remain installed. If it moves, run the standalone pinned bootstrap
with the new Python: receipt-owned manager launchers are atomically refreshed.
For a pre-ledger installation (or edited launcher), the error identifies the
file to inspect and move aside only if it is your obsolete ScriptKit launcher;
bootstrap will not guess ownership. Existing tool launchers may still require
`uninstall <tool>` followed by `install namespace/tool@version` after a base-Python
move: a plain reinstall preserves the old launcher. Local root/state and PATH
integration parents must be private/trusted.
OS file replacement handles process interruption, not a guarantee against every
filesystem/power-loss scenario. Locked Windows pointer files fail closed rather
than changing the old pointer.

Tests execute clean-wheel installs, actual POSIX/PowerShell wrappers over a local
HTTPS fixture (both `powershell.exe` 5.1 and `pwsh` on Windows), real installed
self-update/self-rollback, missing Python, no-PATH roots containing spaces/non-ASCII,
manager-only install followed by registered-tool lifecycle, broken-runtime repair,
child exit propagation, competing updates and phase fault injection. The TLS key
under `tests/fixtures/bootstrap` is intentionally public test data. Platform
results come from the required macOS/Linux/Windows × Python 3.11–3.14 matrix.

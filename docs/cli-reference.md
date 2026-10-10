# CLI and API reference index

Canonical identity is declared by `pyproject.toml`; this reference describes the
provisional v1.5.0 `scriptkit` command and `scriptkit` import. Run `COMMAND --help`
for argument types/defaults. Global `--root PATH`, `--json`, `--quiet` precede
subcommands. Bare invocation and help are read-only. `--version` reports the
installed framework, not a selected tool's version.

| Command | Required input / behavior |
| --- | --- |
| `doctor` | Read-only interpreter, root, active manager and PATH health |
| `registry list` | List explicit trust records |
| `registry add FILE --trust-origin HTTPS_ORIGIN` | Consent to exact Registry contract |
| `registry remove NAMESPACE` | Remove future resolution trust; existing tools remain |
| `catalog NAMESPACE [--offline]` | List exact qualified releases |
| `install NAMESPACE/TOOL@VERSION [--offline] [--dry-run]` | Verify locks, stage, smoke, activate |
| `update NAMESPACE/TOOL@VERSION [--offline] [--dry-run]` | New generation; no implicit latest |
| `rollback TOOL` | Verify and select the retained previous generation |
| `recover TOOL` | Replay a pending activation journal |
| `uninstall TOOL` | Remove owned launchers; retain data/config/generations |
| `self-update --wheel HTTPS_URL --sha256 HASH --version VERSION` | Stage a pinned manager |
| `self-rollback` | Restore previous healthy manager |
| `new-tool PROJECT --spec FILE [--layout standalone\|repository]` | Preview offline scaffold; `--apply` writes |
| `add-command PROJECT --spec FILE` | Preview CommandSpec extension; `--apply` writes |
| `template-upgrade PROJECT` | Preview known template migration; `--check` checks drift |
| `new-collection PROJECT --catalog FILE --origin URL --expires-at UNIX ...` | Render pinned collection and installer |
| `generate-installer PROJECT ...` | Render manager-only verified-bootstrap delegate |
| `validate PROJECT` | Static conformance, no imports/execution of project code |

Authoring commands accept `--apply`, `--check`, `--recover`; consult their help
for required inputs during recovery. Collection/installer pin arguments are
`--bootstrap-sha256`, `--wheel`, `--wheel-sha256`, `--manager-version`.
`validate --help` documents separately consented runtime checks; static success
never certifies arbitrary Python safe. AI has a deliberately separate command
boundary: `python -m scriptkit.ai {context,propose,review,apply}`; disclosure and
application require different exact approval hashes. See [AI guide](ai-proposals.md).

Human diagnostics are readable; `--json` uses the versioned output envelope
(`ok`, `data`, `error`, etc.) on stdout with progress/child output on stderr.
Exit 0 means success, 1 domain/authoring failure, 2 usage, 130 interruption.
A failed command after activation may already have committed: recover and inspect,
not blind cleanup. See [output](output.md) for full machine-output contracts.

## Public API ownership

| Boundary | API reference |
| --- | --- |
| Compatible runtime helpers, app/CLI, config, subprocess, doctor | [Runtime](runtime.md) |
| Structured command/output/progress/prompt contracts | [Output](output.md) |
| Paths, config writes, process trees, interruption | [Runtime IO](runtime-io.md) |
| `ToolSpec`, `CommandSpec`, artifacts, catalogs, locks, plans | [Data contracts](contracts.md) |
| `RegistryStore`, `VerifiedCache`, `Resolver`, source adapter | [Registry](registry.md) |
| `Installer`, `PipBackend`, optional `UvBackend`, receipts | [Installation](installation.md) |
| Streaming artifact limits and deadlines | [Artifact policy](artifact-policy.md) |
| Deterministic rendering, preview/apply, ownership and recovery | [Generator](generator.md) |
| Scaffold runtime, tool/collection/installer composition | [Scaffolds](scaffolds.md) |
| Static diagnostics and approved runtime checks | [Conformance](conformance.md) |
| Typed AI proposal/provider ports and privacy | [AI](ai-proposals.md) |

The manager is the composition root. Runtime, registry, generator and providers do
not acquire each other's authority. See [architecture](architecture.md),
[compatibility](compatibility.md) and [support matrix](support.md).

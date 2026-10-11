# Offline tool and collection authoring

The public CLI offers `new-tool`, `add-command`, `template-upgrade`,
`new-collection`, and `generate-installer`. No AI extras, API keys, template
fetches, package installs or network calls occur during generation. Supply
versioned JSON contracts (examples are bundled in `scriptkit.contracts.resources`).
For tool generation, adapt the bundled contract example's entrypoint to
`example_tool.cli:main`: the generator requires `package.module:function`,
while the general contract also permits single-module entrypoints.
Directories must already exist. Every command previews by default; `--apply`
publishes through the existing journal/lock/hash-CAS engine. Human previews show
a summary, file names and readable unified diffs. Machine failures retain the
structured plan in `data` with a concise `error` and nonzero exit. `--check` fails on
drift. `--recover` rolls forward an interrupted transaction (other required
arguments must still be supplied). Global `--json` produces the standard output
envelope, including failures; authoring errors/drift/conflicts exit 1.

```sh
mkdir my-tool
scriptkit new-tool my-tool --spec tool-spec.json --layout standalone --apply
# Same package, plus an extension-less bin/<tool-name> repository launcher:
scriptkit new-tool my-tool --spec tool-spec.json --layout repository --apply
scriptkit add-command my-tool --spec command-spec.json --apply
scriptkit template-upgrade my-tool --check
```

A command spec is a `CommandSpec`, for example:

```json
{"schema_version":1,"name":"list","help":"List records","arguments":[]}
```

The generated project contains a setuptools package, console script, tool spec,
requirements, configuration example, smoke tests, CI workflow, README and
`AUTHORING.md`. Install the wheel, or `pip install .`; requirements pin the
**generating distribution version**. Repository launchers run with
`python bin/<name>`; explicitly `chmod +x` to execute them directly on Unix.
New files retain the transaction engine's private 0600 policy. Both layouts are
wheel-installable; extension-less launchers are a source-checkout convenience.
`new-tool` preserves an existing project's layout when `--layout` is omitted,
and defaults to standalone for a new project. Supply `--layout` to switch deliberately.

**Release channel:** the runtime-only 1.3.0 wheel does not contain these APIs.
Use the verified GitHub framework 1.5.0 wheel alongside generated wheels
(`pip install --no-index FRAMEWORK_WHEEL TOOL_WHEEL` with actual filenames).
See [onboarding](getting-started.md) for acquisition and provenance. Do not assume
PyPI availability or point generated projects at the old runtime-only wheel.
No existing release is overwritten.

Help, version, doctor and config are read-only. Bare invocation prints help,
never invokes a handler. `doctor` validates optional `--config file.json` (a JSON
object) and reports Python/tool health; `config` displays it without writing it.
Every handler receives the validated object as `arguments["config"]` (empty
when no config file is supplied); it cannot collide with a declared argument.
Do not store secrets in examples or expose them through config output. Global
`--json` opts into the framework envelope. Global flags precede the subcommand:
use `example-tool --config cfg.json config`, not `example-tool config --config cfg.json`. Put handwritten behavior in
`src/<package>/_handlers.py`; placeholders exit nonzero with actionable guidance.
Handlers may return data, integer exit codes (legacy-compatible), or the runtime's
`CommandResult`. Exceptions fail; interruption exits 130. Destructive handlers
must implement explicit user confirmation. `doctor`/`config` command names and
`command`/`config`/`json` argument names are reserved; collisions fail generation.

## Ownership and upgrades

Template 2.0.0 is a composition over the immutable v1 spec renderer; golden hashes
cover all resulting files in both layouts. Explicit `template-upgrade` supports
only the named v1→v2/text-lf-1 migration (and v2 regeneration). No generic or
executable migrations exist. The migration retains ownership of launchers,
changes only hash-matching generated files, and leaves all handwritten modules,
initializers, documentation, config examples, tests and CI intact. The existing
v1 renderer remains available unchanged. Reserved-name collisions in legacy
specs fail before publication. For upgraded Git projects add LF rules for
`/scaffold.json`, `/requirements.txt`, and `/bin/*` to the preserved `.gitattributes`.
Modified generated files require manual
reconciliation; there is no force overwrite. Do not edit generated `tool.json`:
use an external spec with `new-tool`, or `add-command`.

## Collections and manager-only installers

Collections require an actual `CatalogRelease` contract with release artifacts
and dependency locks; the generator never invents download URLs/hashes or claims
that a placeholder artifact exists. Supply a canonical HTTPS origin and explicit
Unix expiry to generate the pinned `registry.json` alongside `catalog.json`.
Artifacts must be copied/published separately, with exactly their declared bytes.
No registration, trust consent, download, or tool selection happens implicitly.

```sh
mkdir collection
scriptkit new-collection collection --catalog catalog-source.json \
  --origin https://tools.example.org/releases/ --expires-at 2000000000 \
  --bootstrap-sha256 "$BOOTSTRAP_SHA256" --wheel "$MANAGER_WHEEL" \
  --wheel-sha256 "$WHEEL_SHA256" --manager-version "$VERSION" --apply
python collection/install.py --bootstrap verified-bootstrap.py --root "$HOME/.scriptkit"
# After publishing the catalog and artifacts:
scriptkit registry add collection/registry.json --trust-origin https://tools.example.org/releases/
scriptkit install collection-name/tool-name@1.0.0
```

`generate-installer` takes the same manager pin flags in a separate directory,
without catalog/origin/expiry. It generates only `install.py` and
`manager-pins.json`. Both forms verify an independently obtained local
`bootstrap.py` against its pinned digest, copy those verified bytes into a private
temporary directory, then delegate to that snapshot using isolated
Python and explicit wheel/version/hash/root arguments. They contain no manager
lifecycle implementation, discovery, sudo, PATH changes, or tool installation.
The manager wheel must be a pinned HTTPS URL, matching the bootstrap contract.
Generation is offline; running the installer downloads the verified wheel.
See [bootstrap.md](bootstrap.md)
for release provenance and verified bootstrap acquisition.

Generator project kinds cannot be switched implicitly: use separate directories
for standalone installer, collection and tool ownership manifests. Regenerate a
collection to update its installer pins; do not run `generate-installer` over it.

CI runs golden rendering, reserved-name/conflict/migration tests, CLI tests,
clean-venv generated-wheel installs (offline resolution), installed help/version/
doctor/handlers and repository launcher execution on the framework's full
Linux/macOS/Windows × Python matrix. Engine tests separately cover crash/recovery
and concurrent writers. Building wheels uses ordinary build tooling; **generation**
is entirely offline, while release acquisition remains explicit.

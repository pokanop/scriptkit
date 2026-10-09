# Bounded installation artifacts

`ArtifactPolicy` is an additive contracts API intended for the next **minor**
release. This change does not change the package version or publish a release.
The existing top-level API golden remains unchanged; the new policy surface has
its own explicit golden.

| Per-artifact limit | Default | Framework hard ceiling |
|---|---:|---:|
| Archive bytes | 64 MiB | 2 GiB |
| Expanded bytes | 128 MiB | 8 GiB |
| Entries | 10,000 | 100,000 |
| Per-member expansion ratio | 134,217,728 | 134,217,728 |
| Command timeout (seconds) | 120 | 3,600 |

Only positive integers are accepted (not booleans, infinity or unlimited
sentinels). Invalid declarations raise `ContractError`. The default ratio is
intentionally redundant with the historic expanded-byte bound: introducing a
small ratio default would reject formerly accepted highly compressible scripts.
Large-artifact consumers should explicitly tighten it (the probe uses 2,000).
Omitting `policy` preserves the registry's 64 MiB transport cap and the
validator's 128 MiB/10,000-entry limits, without imposing a new compressed-size
limit on existing injected byte sources. Passing `ArtifactPolicy()` explicitly
also bounds archive bytes for custom sources. The legacy
`validate_archive(..., max_expanded=...)` API remains available; an explicit
policy takes precedence.

## Composition and registry transport decision

Registry HTTPS/cache transport remains **small-artifact only**. Do not increase
its byte-returning cache limit to transport Torch-class artifacts. The sanctioned
large-artifact source is `RegistryArtifactSource(..., artifact_directory=...)`:
a provisioned, private directory of files named by SHA-256. Download/provisioning
is outside the manager and must finish before resolution. The manager does not
resolve dependencies or contact package indexes. A local artifact is not trusted
merely because its name is a digest: both resolver and installer verify the
size/hash against the currently pinned, unexpired registry catalog.

```python
from scriptkit.contracts import ArtifactPolicy
from scriptkit.manager import Installer
from scriptkit.registry import Resolver
from scriptkit.registry.source import RegistryArtifactSource

policy = ArtifactPolicy(
    max_archive_bytes=2 * 1024**3,
    max_expanded_bytes=4 * 1024**3,
    max_entries=50_000,
    max_expansion_ratio=2_000,
    command_timeout=1_800,
    member_name_grammar="permissive-wheel",  # required for setuptools locks
)
resolver = Resolver(store, cache, policy=policy, artifact_directory=artifact_dir)
source = RegistryArtifactSource(
    resolver, "local", offline=True, policy=policy, artifact_directory=artifact_dir,
)
installer = Installer(root, bin_dir, source, policy=policy)
```

Pass the same policy and directory explicitly to both adapters. Supplying a
larger installer budget does not silently authorize a larger source budget.
The default pip backend and smoke command use the policy timeout. Injected
backends retain ownership of their timeout configuration; use
`PipBackend(timeout=policy.command_timeout)` or
`UvBackend(executable, timeout=policy.command_timeout)`.

## Archive member-name grammars and threat model

`member_name_grammar` accepts exactly two strings (no boolean or other values):

- **`strict` (default):** the existing `INVENTORY_PATH` grammar. Segments start
  with ASCII alphanumeric or underscore; subsequent characters may also contain
  `.`, `+`, `@`, `-`, but not a trailing dot. Device names are forbidden. No
  spaces, parentheses or leading dots. Defaults stay conservative and preserve
  existing trust assumptions.
- **`permissive-wheel` (explicit opt-in):** segments use printable ASCII
  U+0020–U+007E, subject to the denylist below. Leading dots, parentheses,
  brackets, braces, `+`, `,`, `=`, `!`, `#`, `%`, `'`, `^`, `@` are admitted.
  Setuptools locks require this grammar for its launcher manifest, development
  script template and vendored `.lock` file. This replaces the unreleased
  spaces-only proposal, not a previously released API.

| Denied | Reason |
|---|---|
| Backslash | Windows separator / alternate traversal spelling |
| Colon | Windows drive prefixes and alternate data streams |
| `*`, `?` | Windows reserved characters and shell glob patterns |
| Double quote | Windows reserved character and shell quoting |
| `<`, `>` | Windows reserved characters and shell redirection |
| Pipe | Windows reserved character and shell pipeline |
| Backtick, `$` | Shell command/variable substitution |
| `;`, `&` | Shell command chaining / background execution |
| Controls and all non-ASCII | Control injection and cross-platform normalization ambiguity |
| Empty segments, `.` or `..` | Absolute paths, duplicate separators, traversal |
| Leading space, trailing space/dot | Ambiguous display and Windows filename normalization |
| Leading `-` or `~` | Option injection and home-directory expansion |
| Consecutive spaces | Ambiguous whitespace spelling |
| CON, PRN, AUX, NUL, COM0–9, LPT0–9 before first space/dot | Windows device aliases, case-insensitively |

Paths remain relative POSIX paths with `/` separators. A single final `/` is
allowed as a directory marker, not an empty path segment. Catalog artifact paths
and registry URLs retain the unchanged `INVENTORY_PATH` grammar. Names are
validated, never rewritten on disk; case-collision, traversal, mode, encryption
and compression checks remain enforced. This grammar is **not** shell escaping:
consumers must continue to pass arguments as arrays, never interpolate names into
shell commands. The additive API golden records the new keyword; release still
requires a minor bump, outside this change.

## Threat model and residual bounds

Archives are untrusted even after transport integrity checks. Files are spooled
one at a time into an inactive, private generation before any extraction or
backend execution. Full CRC validation uses 1 MiB reads. Traversal, links,
encryption, malformed ZIPs, case collisions, file/directory collisions and
unsupported compression continue to reject. Wheel identity metadata is bounded
at 1 MiB. Only top-level `.dist-info/METADATA` identifies the installed wheel;
nested vendored distributions cannot supply or override its identity. Inventory
and receipt verification hash files incrementally too.
Generation failures never activate; retained failed generations need explicit
administrative cleanup. Disk budgets are per artifact, not aggregate quotas.
Private roots must not be writable by another principal (Windows users must
also provide appropriate ACLs). Policy limits do not sandbox malicious Python
code in an explicitly trusted package.

Old `fetch() -> bytes` sources remain compatible, but may materialize one entire
artifact. Large sources must provide `fetch_into(artifact, destination)` and
bound their writes; source adapters are trusted injected code, not an adversarial
plugin boundary. The manager independently rechecks the staged bytes.

## Verification

Required tests generate a wheel with over 64 MiB stored bytes, over 128 MiB
expanded data and over 13,000 entries, then exercise resolve, authorization,
spooling, validation, lock verification, offline pip staging, smoke and activation.
They never download Torch. The manual `large-artifact.yml` workflow runs:

```sh
python tools/large_artifact_probe.py evidence --torch
```

This requires Linux x86_64 CPython 3.13, several GiB of download/disk space and
explicit operator consent. It asserts the exact Torch wheel hash, writes the
complete catalog with immutable transitive wheel hashes, and imports Torch in
the smoke command. Dependency discovery happens only in the evidence setup,
never in the manager. The catalog/logs should be retained with the run.

The probe runs manager installation in a fresh interpreter after downloading,
printing manager-process and backend/smoke-child peak RSS separately on Linux.
`--prepared-torch` reuses a directory of already downloaded wheels without
mixing pip download memory into manager measurements.

Before any install, the probe scans every wheel member and writes
`member-scan.json`: counts admitted only by the opt-in, grouped by grammar rule,
and every still-rejected name. A rejection stops the probe rather than widening
the grammar. The complete real Torch set has passed full manager installation
with the permissive grammar; retain the scan, immutable catalog, output and RSS
measurements as evidence.

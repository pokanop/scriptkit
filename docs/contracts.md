# Data contracts (schema version 1)

`scriptkit.contracts` is a standard-library-only ownership lane. Public records
are frozen dataclasses, with immutable tuple collections and typed constructors.
Both constructors and parsers validate; JSON arrays decode to tuples. Ports are
structural `typing.Protocol`s, not service locators or concrete adapters.

```python
from scriptkit.contracts import ToolSpec, resource_text

spec = ToolSpec.from_json(resource_text("ToolSpec.example.json"))
assert ToolSpec.from_dict(spec.to_dict()) == spec
canonical_bytes = spec.canonical_json().encode("utf-8")
```

## Wire format and validation

All fields are required, including nullable fields; no implicit defaults or type
coercion. Unknown fields (including `hooks`, `post_install`, executable commands
and provider-specific payloads), duplicate JSON keys, non-finite numbers and
unknown schema versions fail with `ContractError(ValueError)` and a field path.
Booleans are not integers. Code strings are not evaluated, entry points are not
imported, and proposal contents confer no authorization to install or execute.
An entrypoint is only a `module.path:callable` reference, not a shell command.
All string fields, including descriptions, help, choices, defaults and AI text,
reject C0/C1 control characters and DEL except newline and tab. The exported
schemas enforce the same rule; untrusted terminal escape sequences never become
valid contract data.

Names are portable lowercase identifiers, limited to 64 characters. Installation
destinations are lowercase relative POSIX paths; artifact and inventory paths
preserve ASCII case (for example `PIL/Image.py`, `Scripts/python.exe`, `LICENSE`
and `.dist-info/METADATA` under a package prefix). Paths are limited to 240
characters: no absolute
paths, drive letters, separators other than `/`, empty/dot/traversal components,
Windows device names in any case, trailing dots/spaces or control characters.
Receipts reject casefold-equivalent file names and file/directory collisions,
even on case-sensitive hosts, so their inventories are portable. This deliberately
conservative v1 grammar is not an arbitrary host-path format. Filesystem adapters
must ALSO enforce root confinement, reject symlink escapes and handle TOCTOU;
syntactic validation is not a filesystem sandbox. Platform names are `linux`,
`macos`, `windows`; architectures are `x86_64`, `arm64`. Platform adapters own
translation from host spellings (`AMD64`, `aarch64`, `darwin`).

Tool/catalog versions use SemVer 2.0. Package identifiers are normalized lowercase
identifiers starting with an alphanumeric and containing alphanumerics, `.`, `_`,
`-`, `+` or `@` (including qualified winget IDs, apt `g++`/`libstdc++6` and brew
`python@3.12`/`openssl@3`); they are not filesystem paths. Package versions are exact numeric-leading
backend versions (not Python-only SemVer, moving tags or solver expressions).
Adapters must additionally validate their backend's version grammar and verify
that fetched metadata matches the exact lock. SHA-256 values are
64 lowercase hexadecimal characters. Python requirements use explicit inclusive
minimum and exclusive maximum `3.x.y` release bounds (3.11 or later; v1 supports
minor releases through 99), avoiding ambiguous specifier intersections. Every
release has locks covering exactly its declared platforms; backend/platform
pairs are unique, every lock's Python interval lies inside the tool's, and the
backend intervals for a platform must overlap. Install plans select all backend
locks for one platform and one concrete compatible Python version.

Canonical JSON sorts object keys recursively, preserves array order, emits no
insignificant whitespace, escapes non-ASCII characters and contains only integer
numeric fields. UTF-8 encoding of this string is the canonical digest input; the
trailing newline in resource examples is NOT part of it. This is ScriptKit's
canonical format, not a claim of RFC 8785 implementation. Array order is meaningful
and is never silently sorted by parsers. Runtime timestamps are UTC Unix seconds,
not strings with potentially ambiguous timezones.

## Records and responsibility

| Record | Meaning |
| --- | --- |
| `ArgumentSpec`, `CommandSpec` | Declarative CLI inputs; duplicate names, incompatible defaults/choices and ambiguous positional ordering fail. Flags are optional booleans; choices are strings in v1. |
| `ToolSpec` | Tool identity/version, entrypoint reference, Python/platform support and commands. No generated code, dependencies with floating versions or hooks. |
| `DependencyLock` | Exact packages, artifact hashes/sizes, Python range, platform and backend (`pip`, `apt`, `brew`, `winget`). Backend adapters interpret exact package versions, never as shell fragments. Empty package sets are valid explicit locks. |
| `CatalogRelease` / `ToolRelease` | Immutable catalog identity/version; each tool release includes a validated spec, source artifact and complete lock set. Duplicate tool/version pairs fail. Artifact paths are relative to the catalog's adapter-owned root, not network URLs. |
| `InstallPlan` | Pinned catalog digest, release snapshot, platform/Python selection, confined relative destination and new/previous generation IDs. Planning is not execution. |
| `Receipt` | One successful staged installation: full plan, completion time, unique relative file hash/size inventory. Inventory paths are relative to the plan destination and cannot overlap as file/directory. No failed or half-installed receipt is valid evidence of success. |
| `Generation` | Nonempty group of receipts with matching generation lineage, unique tools and disjoint destinations on one OS/architecture. Python versions may differ because each tool owns an isolated environment and its plan independently validates compatibility; describes a completed candidate snapshot, not whether it is active. The manager atomically owns activation/rollback state and interruption journals. |
| `AIProposal` | Provider/model/request identity, optional base-spec digest, rationale and validated candidate ToolSpec. Proposal acceptance/review, credentials and execution are intentionally outside this envelope. |

`Artifact`, `LockedPackage`, `Platform`, `PythonRequirement` and `ToolRelease`
are embedded typed records inheriting the containing document's version. The
nine versioned document types are exposed as `CONTRACTS`.

## JSON schemas and compatibility/migration policy

Wheels and sdists include `<Contract>.schema.json` (Draft 2020-12) and
`<Contract>.example.json` in `scriptkit.contracts.resources`. Read them with
`resource_text()` or `importlib.resources`, never a checkout-relative path.
`Contract.json_schema()` derives the structural schema from the same dataclass
fields and constraints the strict codec uses. JSON Schema validates structural
constraints; **cross-field invariants require `Contract.from_dict/from_json`**.
A structural schema pass is not sufficient to authorize an installation.

- Version 1 is the first contract release; there is no legacy v0 wire format.
  The inherited runtime's package version 1.3.0 is unrelated to schema versions.
- Readers reject unsupported older AND newer versions; no best-effort parsing,
  field dropping, hook execution or implicit downgrade.
- Within a schema version, existing payloads remain readable with identical
  canonical bytes. Fields cannot be added (even "optional" fields break strict
  readers), removed, renamed, retyped or assigned new semantics. New enum values
  or expanded grammars require a version bump too. Fixing a validator to match
  the already-documented rules is a bugfix, not a compatibility license.
- A future version must ship its own schema, golden examples and explicit pure
  migration function. A migration must validate its source version first, map
  fields without I/O or hooks, then validate its output. Lossy/ambiguous migration
  fails and requires human input. Never silently migrate a signed/hashed catalog:
  re-canonicalize, re-hash and re-authorize the new document.
- Keep supported old readers/fixtures until a documented major-package-version
  deprecation/removal. New document versions alone do not authorize dropping old
  readers. Forward compatibility means explicit rejection, not ignoring data.
- `tests/fixtures/contracts/tool-v1.json` is a permanent baseline, independent of
  resource regeneration. v0/v2 fixtures must fail today. When v2 lands add v1→v2
  expected output and failure fixtures; do not rewrite the v1 baseline. There is
  intentionally no invented v0 migration implementation.

Regenerate resource files with `python tools/export_contracts.py`. Tests detect
schema drift and validate all nine examples with an independent JSON Schema
validator. `jsonschema` is a **test-only** dependency, never a runtime dependency.

## Injected interfaces and failure semantics

`ports.py` defines `ArtifactSource`, `PackageBackend`, `FileSystem`, `Clock`,
`Output` and `ProposalProvider`. Consumers accept these interfaces in constructors
or calls; adapters must not be instantiated or credentials loaded at import time.
Registry owns verified artifact reads, package adapters own exact lock application,
manager owns staging/activation/cleanup, provider adapters own remote transport,
and the composition root wires them. Synchronous calls propagate ordinary errors
and `KeyboardInterrupt`; cancellation is never translated to a success receipt.
Atomic methods must preserve the old visible state on failure. These are adapter
obligations, not guarantees supplied merely by implementing a Protocol.

`test_offline_injected_ports` demonstrates a local filesystem catalog, verified
artifact bytes and fake implementations of every port, including a failed hash,
missing catalog and interrupted backend. No production credentials are needed.
Real package managers, atomic filesystem implementations, artifact verification
adapters, remote providers and activation journals are the later owning tickets,
not stub production implementations in the contracts package.

# Trusted registry resolution

`scriptkit.registry` is an opt-in library API (no CLI/UI in this slice). It never
imports the manager, executes tools, resolves public package indexes, or installs
packages. The registry owns trust records, transport, cache and exact resolution;
contracts own release/lock validation. The only runtime dependency is the narrow
`scriptkit.state` atomic persistence adapter. Transport, StateIO and clock are
injectable; state uses compare-and-swap and propagates interruptions.

## Trust and usage

Obtain an exact catalog SHA-256, byte size, expiry and HTTPS origin via a trusted
out-of-band publisher channel. Construct `Registry(1, namespace, origin,
Artifact("catalog.json", digest, size), expires_at)` and call
`RegistryStore(path).add(registry, consent_origin=origin)`. A caller must obtain
interactive consent displaying that exact origin, or explicit noninteractive
configuration (such as `--trust-origin`). No origin is implicitly trusted.
`list()` and `remove(namespace)` manage these records. A namespace cannot be
silently replaced: removal and new consent are required to change pins.

Use `Resolver(store, VerifiedCache(cache_path)).list(namespace)` for qualified
names, then `resolve("namespace/tool@1.2.3", platform=Platform(...),
python_version="3.13.0", generation="generation-one", destination="tools/name")`.
There is no unqualified lookup, latest/range resolution or fallback between
registries. Catalog name must equal its registered namespace.

For POK-621's injected `ArtifactSource` port, use
`RegistryArtifactSource(resolver, namespace, offline=False)`. Its
`catalog(name, version)` requires the exact bound namespace and catalog version;
`fetch(artifact)` accepts only artifacts in that pinned catalog and rechecks
registration/expiry on every call. It never silently follows replacement pins to
fetch an artifact that the new catalog does not authorize.

The result contains the exact origin and pinned catalog, full InstallPlan with
Python/platform constraints, selected transitive artifact hashes, and canonical
SHA-256s of embedded platform locks. Locks are retrieved as part of the pinned
catalog, not from a mutable secondary endpoint. Every selected artifact is
retrieved and verified before a plan is returned. Lock publishers must enumerate
the full transitive closure; a future installer must not infer missing packages
from an index. Native package artifacts are opaque verified bytes; their backend
is responsible for safe installation. Pip inputs are wheels only.

## Authentication boundary

**A same-origin checksum is integrity, not publisher authentication.** HTTPS
verifies the server, not an independent release signer. This implementation does
not claim signature verification, TUF rollback protection, or transparency-log
verification. Trusted release identity comes from the caller's independently
verified catalog pin. If using signatures/attestations, verify publisher identity,
repository/workflow and exact digest externally before consenting to the pin.
Never bootstrap trust by automatically copying a remote checksum into a record.
A malicious explicitly trusted publisher can ship malicious Python code.
**A virtual environment is not a code-execution sandbox.**

## Cache and metadata policy

Unknown schemas/fields, duplicate keys, malformed versions and unsafe artifact
paths are rejected by strict contracts. Catalogs are limited to 4 MiB; individual
artifacts default to 64 MiB. Transport reads at most the declared size plus one,
uses a 30-second socket timeout, refuses redirects and encoded responses. HTTP,
file URLs, credentials, queries and encoded/traversal origin paths are forbidden.
Fixture tests inject a local in-memory transport, not an insecure production mode.

Intended hosting is direct static HTTPS file serving (for example nginx or an
S3 REST endpoint with no redirects), with an origin such as
`https://registry.example/releases/v1-2/`. Paths in the origin use lowercase
letters, digits, underscores and hyphens; artifact-relative paths support dots.
GitHub Releases redirecting asset URLs are deliberately unsupported. Mirror those
assets onto the consented static origin; do not relax redirects or reuse an
expiring signed URL as a registry origin. This is a hosting contract, not a claim
that a production registry has already been deployed.

Cache keys are SHA-256; contents are rehashed and size-checked on every read.
Publication is atomic only after verification. Corruption fails closed rather
than silently replacing evidence. A complete cache supports `offline=True` with
zero network calls. Missing entries and expired metadata fail even offline; no
stale-if-error or implicit refresh. Expiry is pinned locally and relies on the
injected/system clock. Remove/re-add with new explicit consent to renew metadata.
Cache GC is caller-owned; deleting unused blobs is safe. Interrupted downloads do
not publish cache entries; a crash during state publication may require explicit
lock recovery per [runtime IO](runtime-io.md). Concurrent cache publication is
best-effort: a losing writer returns its own verified bytes without claiming the
cache write succeeded. Other IO errors and cancellation still propagate.

State and cache directories must be private and trusted, including all parents;
this is not protection from a hostile local user changing parent symlinks. Windows
requires directory ACLs; POSIX file modes are not Windows ACLs.

## Archive boundary

Wheel ZIPs and legacy `*.scripts.zip` bundles are the only tool sources. Legacy
bundles contain only portable `.py` files/directories; no shell/install hooks,
symlinks, devices, traversal, case collisions or file/directory collisions.
Archives are inspected without extraction/execution, limited to 10,000 members
and 128 MiB expanded data, with stored/deflated entries only and CRC validation.
Wheel inspection is structural safety, not full wheel semantic validation;
installation adapters must still validate wheel metadata/tags and root confinement.
No archive inspection makes Python source trustworthy.

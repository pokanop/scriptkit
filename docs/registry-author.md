# Publish a trusted tool collection

A registry publishes **data and artifacts**, not install scripts or hooks. There
is no global registry or namespace registration service. Publishers own the trust
and availability of their HTTPS origin; users explicitly consent to an
independently verified Registry record.

1. Scaffold, manually extend, validate and test your tool using the
   [author guide](scaffolds.md). Review any AI-generated code exactly like manual
   code. Build a wheel with a unique distribution name, exact version and declared
   entrypoint. Never reuse an existing version for different published bytes.
2. For each supported OS/architecture/Python range, resolve and acquire every
   transitive **wheel** in advance. Make `DependencyLock` records with backend
   `pip`, exact normalized names/versions and each wheel's path, SHA-256 and size.
   Include the verified framework wheel when the generated tool requires it.
   The installer uses `--no-index --no-deps`: missing dependencies do not get an
   implicit index fallback. Native wheels must match the target interpreter and OS.
3. Construct a `ToolRelease(tool, artifact, locks)` and a versioned `CatalogRelease`
   with your namespace. Examples and JSON schemas are bundled in
   `scriptkit.contracts.resources`; see [contracts](contracts.md). Use canonical
   serialization (`record.canonical_json()`) before computing byte length/digest.
4. Host catalog and artifacts at a direct, stable HTTPS origin such as
   `https://tools.example.org/releases/v1/`. Serve exactly the recorded bytes, no
   compression/redirects. **GitHub Releases redirecting asset URLs are suitable
   for manager bootstrap, not this registry transport.** Mirror verified release
   artifacts on your consented static origin. Never relax transport checks.
5. Construct `Registry(1, namespace, origin, catalog_artifact, expires_at)` with an
   explicit Unix expiry. Distribute this record and authenticated catalog identity
   through your independently verified publisher channel. Same-origin checksums
   alone do not authenticate a publisher. If using signatures/attestations, verify
   exact signer/repository/workflow/digest externally before consenting.
6. Optionally use `new-collection` to render `catalog.json`, `registry.json` and the
   pinned manager-only installer from those inputs. It never publishes, trusts or
   installs anything automatically; see [collection commands](scaffolds.md).
7. On a clean supported host, explicitly add trust, list the catalog, install/run,
   update to another exact version, rollback and uninstall. Test failure and
   interrupted activation too. Record the artifact pins and results.

Publish a **new** catalog pin for additions/updates. Users remove the old trust
record and add the new one with renewed origin consent; neither expiry nor origin
changes are silently refreshed. Removal does not revoke already-installed code or
its offline rollback targets. Retain old immutable artifacts for reproducibility.
A complete verified cache permits offline installs until trust expiry; partial or
poisoned caches fail closed.

Local private state is trusted; a malicious same-account process is outside the
boundary. Catalog validation and archive confinement prevent structural attacks,
not malicious Python behavior from a trusted publisher. Venv isolation separates
dependencies, **not permissions**. See [registry policy](registry.md),
[threat model](security-certification.md) and [security policy](../SECURITY.md).

# Executable trust-boundary assessment (POK-630)

This is a threat model and repeatable adversarial assessment, not a claim that
arbitrary Python packages or AI output are safe. Independent security review
must identify the exact reviewed commit and remaining risks before acceptance.
The issue/PR records current-head CI and reviewer evidence; this document does
not predeclare approval or general availability.

## Assets, actors and assumptions

Protect user files/config/credentials, installed-tool availability, generation
lineage, launcher ownership, approved context/diffs, immutable package identity
and the user's choice of publisher/provider. Adversaries can serve hostile ZIPs,
substitute downloads/model output, poison cache bytes, supply misleading catalog
names, replay saved plans, interrupt activation or cause competing writers.
Providers and packages are not trusted simply because their transport is HTTPS.

The OS, Python interpreter, explicitly selected backend, composition root and
private state directory/parent ACLs are trusted. A local attacker able to rewrite
both receipts and payloads already has the user's execution privileges: receipts
are integrity inventories, not signatures against that attacker. Concurrent
hostile replacement of trusted parent directories is outside the filesystem
contract. Kernel locking covers cooperating local processes, not distributed
filesystems. Directory power-loss durability is not promised.

### Data and authority flow

1. User consents to exact registry origin → pinned catalog + expiry → strict
   namespace/version/platform resolver → typed, hash-bound installation plan.
2. Source reauthorizes current pins → cache rehash/download limits → safe ZIP
   validator → wheel/lock identity validation → offline package backend.
3. Backend creates a fresh generation → **executes trusted package smoke check**
   → records receipt → journal/owned launchers → atomic active pointer.
4. Selected project files → context/destination approval → bounded optional
   provider → strict proposal parsing → static review → diff-hash approval →
   recoverable apply. No implicit dependency/install/publish/commit authority.
5. Bootstrap is separate from per-tool installation: pinned release artifact,
   receipt, stable launcher and manager-generation switch with repair/rollback.

## Runnable evidence map

All paths below are under `tests/`. Each row includes success and rejection/
recovery tests; these execute in the ordinary required suite, not an opt-in
checklist. New composed canaries are in `test_security_certification.py` and
`test_security_ai.py`, using the same `RegistryArtifactSource` and `Installer`
contracts consumed by pokanop/scripts.

| Boundary / threat | Positive control and adversarial evidence |
| --- | --- |
| Registry trust, rollback, expiry, namespace confusion | `test_registry.py`: online/offline roundtrip, consent/shadowing, exact versions/expiry; `test_security_certification.py`: expiry/removal/repoint/old-catalog substitution **after planning** rejected before any new generation, namespace cannot adopt existing tool, exact origin consent. Explicit remove-and-add by the owner can deliberately select an older catalog; there is no claimed signed monotonic publisher history. |
| Download, poisoned cache, hash and lock substitution | `test_registry.py`: successful cached replay and changed bytes; `test_security_certification.py`: content/size/symlink cache poison and catalog poison fail before execution; `test_registry_review.py`: concurrent cache publication; `test_installer_wheels.py`: valid wheel identity versus metadata/path/source substitutions; `test_security_certification.py::test_saved_plan_cannot_substitute_release_or_lock` rejects an internally consistent forged saved release/lock through `Installer.install`; `test_locked_wheel_identity_is_enforced_before_backend` rejects a hash-valid wheel with substituted metadata through that same installer; `test_installer_rechecks_bytes_from_injected_source` rejects unpinned bytes even when the injected adapter authorizes them. |
| Archive and extraction | `test_registry.py`: safe bundles, malicious paths, symlink, collisions, expansion; `test_registry_review.py`: malformed CRC/compression; `test_artifact_policy.py`: bounded archive policies; composed hash-valid traversal/absolute/link/FIFO bundles in `test_security_certification.py` fail before backend invocation and never publish launchers. |
| Receipts, launchers, transactions | `test_installer.py`: real install/update/rollback/uninstall and interruption at every phase; `test_installer_recovery.py`: process death, repeated recovery, locked Windows pointer; composed plan replay/content/receipt/launcher tampering and concurrent lock refusal preserve the previous runnable generation. `test_installer_review.py` checks ownership after interpreter/path changes. |
| Bootstrap / manager updates | `test_bootstrap.py`: real installed-wheel install/repair, wrong identity, symlinks, interruption, failed child, foreign ownership and bounded fetch; `test_bootstrap_shells.py`: native sh/PowerShell entrypoints and exit codes; `test_bootstrap_review.py`: changed interpreter and interrupted ownership publication. |
| Config, keyring and private data | `test_safe_config.py`: references/precedence/CAS versus malformed config and forbidden literal secrets; `test_state_io.py`: private permissions, stale locks, write failure, symlink refusal and competing writers; `test_security_ai.py`: synthetic `.env`/home/key canaries absent from both provider wire contexts, no cloud key on loopback, sanitized keyring traceback; `test_ai_providers.py`: missing key, key echo, auth/timeouts and no-write CLI failure. Legacy `Config` compatibility is not claimed to enforce the new schema. |
| Package execution / logs | `test_installer.py`: real pip and optional uv install/smoke; `test_installer_recovery.py`: offline uv arguments; `test_execution.py` and `test_process_cleanup.py`: output/time bounds, cancellation and owned process cleanup; composed package failure canary absent from exception/stdout/stderr/logs. Tool execution itself remains trusted, not sandboxed. |
| Malicious AI output / replay | `test_ai.py`: review/apply success, prompt injection/forbidden paths/dependencies, stale context, symlinks, interrupted apply, top-level code **not executed**; `test_security_ai.py`: an approved diff cannot authorize substituted output or same-base replay after apply; `test_ai_providers.py`: two wire shapes, endpoint/redirect rejection, output/request/token limits, retries and cancellation. |

## Reproduce and retain evidence

```sh
python -m pip install -e '.[dev,rich]'
python -m pytest --junitxml=results.xml
python tools/verify_results.py results.xml
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m mypy
python -m build
python tools/check_artifact.py dist/pokanop_scriptkit-1.5.0-py3-none-any.whl
python tools/check_artifact.py dist/pokanop_scriptkit-1.5.0-py3-none-any.whl --rich
```

`check_artifact.py` copies tests to a clean directory, installs the wheel in a
fresh venv and verifies imports come from that venv, not this source tree.
Required CI runs source and both installed-wheel variants on **real Ubuntu,
macOS and Windows runners**, Python 3.11–3.14, uploading per-runner JUnit/coverage.
Windows native locking and PowerShell probes are not replaced by Linux mocks.
The optional uv path runs where uv is provisioned; a Linux-only Windows skip is
not Windows evidence. Inspect skips as well as pass counts.

For the migrated consumer, reproduce against accepted scripts merge
`68f12bc5b4baf845916a77e5e47dd0e5b6ab0da6`, using its pinned released framework:

```sh
python -m pip install -e '.[dev,keyferry,aikit,pluck]'
python -m pytest tests/test_external_runtime.py tests/test_manager_migration.py tests/test_manager_recovery.py tests/test_manager_legacy.py tests/test_manager_review.py
```

Its accepted PR #54 records 8/8 required checks, including real three-OS migration
runners, on head `7eca051b9b7e755d1cbb8a3dc55d06b3c920138c`. This historical evidence
is not a claim that a future framework version has already been adopted there.

## Least privilege, consent and recovery guarantees

- Run as the user, never elevate automatically. System dependencies require
  explicit user provisioning. Backend wheel installation uses offline/no-index,
  no implicit dependency resolution, and no ambient pip/uv/Python config.
  This does **not** strip every OS environment variable or prevent network/file
  access by installed code. Venvs separate dependencies, not privileges.
- Registration consents to a publisher origin; consumer local-project
  registration consents to executable source. Neither makes malicious code
  harmless. Install includes execution of smoke checks, before activation.
- Offline resolution never fetches missing cache entries or ignores expiry;
  no paid inference fallback. Bootstrap's first install requires its explicitly
  pinned release source. An already-installed generation can run offline.
  Rollback verifies local inventory, not fresh registry expiry: it is recovery
  of previously installed code, not authorization to download a new release.
- Failed staging cannot become active without smoke/receipt validation. Old
  generations and user config remain; interrupted journal recovery is explicit
  and repeatable. Uninstall removes only owned launchers and retains generations.
  Foreign/edited launchers cause refusal rather than destructive repair.
- AI context is explicitly selected, not repository/home upload. Preview and
  diff approval are separate hash-bound acts. Arbitrary code can still be
  expressed using allowed imports; humans must review semantics. Secret regexes
  cannot identify arbitrary strings/encodings or all personally identifying data.

## Release/merge controls and residual risks

The new fixtures run in the existing required runtime/static/release-rehearsal
jobs. `required-quality` fails closed on failed, skipped or cancelled prerequisite
jobs. Release calls the same quality workflow, verifies signed provenance and
checks a versioned main-ancestor commit; dispatch cannot trigger PyPI publication.
PyPI additionally requires the enable variable and protected environment.

**Merge enforcement is currently procedure-only.** At the round-1 assessment,
`main` has neither branch protection nor a branch ruleset: `required-quality`
reports a result but does not technically prevent merging or direct pushes.
The agent push identity is a repository admin and can bypass the `v*` tag
ruleset. A direct push to main can satisfy the release main-ancestor check;
that check is not evidence of PR review. Controller/human must verify green CI
and independent exact-head approval before merging, but the token can bypass
that procedure. Do not equate procedural instructions with enforced controls.

AC 3 remains pending the workspace owner's decision: enforce a main ruleset
requiring PRs, `required-quality`, an approval, and blocking force-push/deletion
(without an agent/admin bypass), or explicitly accept procedure-only gating.
No repository administration settings are changed by this PR.

PyPI's environment does enforce a required reviewer with admin bypass disabled
and self-review blocked. Its only reviewer is the same identity used by agents,
so a tag pushed by that identity currently has no eligible approver. This fails
closed but needs the owner's confirmation of the intended publishing identities.
Independent security approval remains **separate from passing tests**.

No runtime exploit was demonstrated by the added canaries. The stale SECURITY.md
claim that registry/manager/AI were unimplemented was corrected rather than left
as misleading guidance. Round-1 mutation checks exposed missing composed
release/lock and installer hash evidence; the three named tests above cover those
guards. The merge-control acceptance gap remains explicitly unresolved pending
the owner's decision, rather than being waived as a later ticket.

Residual risks, not security guarantees: same-user private-root compromise;
trusted publisher/package code and inherited environment; undetected secrets in
user-approved context; provider data retention; dependency vulnerabilities after
this assessment; local filesystem/power-loss limitations; no signed monotonic
catalog history; procedure-only main merge/direct-push controls and admin tag
bypass; the currently unapprovable same-identity PyPI publish flow. Known consumer POK-648 Windows full-uninstall exit-code behavior
is pre-existing and does not establish a trust-boundary bypass. Voxtract remains
POSIX-only; install/help success is not end-to-end Windows media support.

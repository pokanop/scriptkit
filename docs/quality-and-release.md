# Quality and release contract

## Required checks

`required-quality` is the stable branch-protection check (GitHub Actions app 15368).
It always runs and rejects failed, cancelled **and skipped** prerequisite jobs:

- `runtime`: all 12 Linux/macOS/Windows × Python 3.11–3.14 cells, unit/integration
  tests, wheel built through sdist, metadata validation, two installed-artifact
  suites outside the checkout (bare and Rich, no requests).
- `static-and-coverage`: Ruff syntax/correctness lint, formatting, strict mypy for
  new modules, gate regression tests, and at least 90% changed executable framework
  line coverage relative to the PR base. Coverage includes all runtime modules;
  branch measurement is enabled. Critical failure/recovery branches require
  explicit tests in review, not just a percentage. A subprocess self-test proves
  pytest and mypy reject deliberately broken inputs using the project's discovery
  and policy configuration; mypy targets Python 3.11 even on the 3.14 runner.
  Every remote workflow action pin (including release-only actions) must resolve
  through GitHub's git-commit API; annotated tag objects and lookup failures fail CI.
- `dependency-security`: audits the resolved development and Rich dependency graph,
  including build tooling. No advisory suppressions; network/audit failures fail CI.
- `release-rehearsal`: two clean git-archive builds, isolated pinned backend builds,
  byte-identical wheel/sdist comparison, all package resources and license present,
  strict metadata checks, SHA256 verification, and installed bare/Rich suites.

Every runtime/installed suite must execute at least 150 passing tests (157 currently;
platform/Rich-specific individual skips are allowed). Empty/all-skipped suites fail.
No paths filters, continue-on-error, privileged PR events, or secret-bearing PR jobs.
Job timeouts cover hangs/interruption; gate tests cover failure and recovery. Coverage
and release artifacts are retained in Actions. Python 3.14 is the current stable
release; extend the explicit matrix when a new stable Python is released.

## Reviewed migration debt, not exclusions for new code

The twelve imported 1.3.0 modules are explicitly listed in the mypy/formatter
configuration to preserve the foundation's unmodified runtime. They still receive
syntax/correctness lint, full tests, and changed-line coverage; new modules get
strict typing and formatting automatically. Ruff's default E4/E7/E9/F rules apply
to all source, tests and tooling with no lint exclusions. There are
**no changed-line coverage exclusions** within `src/scriptkit`; tests/tooling are
not framework runtime and have dedicated behavioral tests. Removal/expansion of
legacy exceptions or reduction of test floors requires independent review.

Rich 14 is now supported (`>=13.9.4,<15`) and the latest allowed release is tested;
the installed-wheel suite also tests the exact 13.9.4 lower bound on Linux/Python 3.11. Windows console control-event
injection remains untested (the inherited POSIX signal test is platform-specific).

## Reproducible release and provenance

Run `python tools/rehearse_release.py` from a clean committed tree with an empty
`dist` directory, after installing `.[dev,rich]`. It builds **committed HEAD**, not
uncommitted edits. The backend and wheel versions are pinned; source timestamps
come from the commit. Sdist timestamps/ownership/gzip metadata are normalized;
both builds must be byte-identical on the same interpreter/platform. This does
not claim cross-platform/interpreter byte identity. Build tools may be updated
only with a reviewed rehearsal. `dist/SHA256SUMS` and `dist/build.json` record hashes
and source commit; the latter is metadata, not a cryptographic attestation.

After this workflow lands on main, **Release → Run workflow** performs the full
matrix/rehearsal and, only on `main` or protected `v*` tags, signs every artifact
with GitHub OIDC provenance. Dispatches on other branches cannot sign. Verification
binds this repository, workflow, commit and exact source ref and rejects self-hosted
runners. Dispatch never publishes. PR CI cannot obtain an OIDC token or attestations
write permission; it tests checksums/reproducibility but cannot sign provenance.
A protected `v<pyproject version>` tag can publish only after every gate and the
`pypi` environment approval. Publication uses the same verified artifacts, not a
rebuild. Failed verification or upload stops publication; there is no skip-existing
or automatic retry that could silently combine partial releases.

Consumers must verify the expected ref, not just the workflow identity. Substitute
an independently reviewed commit and its release tag (or `refs/heads/main` for a
rehearsal) in this command; do not trust values supplied solely with the artifact:

```sh
gh attestation verify artifact.whl --repo pokanop/scriptkit \
  --signer-workflow pokanop/scriptkit/.github/workflows/release.yml \
  --source-digest "$EXPECTED_COMMIT" --signer-digest "$EXPECTED_COMMIT" \
  --source-ref "$EXPECTED_REF" --deny-self-hosted-runners
```

Record a green **Release → Run workflow** on `main`, including provenance generation
and verification, in POK-616 after merge before the provenance acceptance criterion
is complete. PR CI validates pins but intentionally cannot exercise OIDC signing.

## Owner setup before the first real publication

1. Resolve the ScriptKit collision with johnlindquist/kit and confirm ownership/
   availability of provisional distribution `pokanop-scriptkit`. Do not publish
   under an unrelated name or create a tag merely to exercise publication.
2. In PyPI configure a pending/trusted publisher: owner `pokanop`, repository
   `scriptkit`, workflow `release.yml`, environment `pypi`. No long-lived API token.
3. **Hard publication precondition:** retain `can_admins_bypass=false` and
   prevent-self-review on `pypi`, and add an independent human reviewer with
   repository access, distinct from the shared agent identity. The current sole
   reviewer is the shared admin account, so it cannot approve its own runs.
   Deployment tags must remain restricted to `v*`. Configure trusted publishing
   on TestPyPI separately if desired (not implicitly enabled).
4. Only after naming, publisher, independent reviewer, bypass-off and tag policy
   are confirmed, set the
   repository variable `PYPI_PUBLISH_ENABLED=true`. It is absent/disabled by default.
5. **Before the first manager release, bump both distribution/runtime versions and
   update versioned artifact references/tests.** Do not reuse `1.3.0`: the published
   `runtime-1.3.0-7c17061` wheel already identifies different, runtime-only bytes.
   Record the new version and release hashes in the release ticket. The publishing
   workflow rejects `1.3.0`, even if metadata/tag equality would otherwise pass.
   Create a version tag pointing to an approved commit on main. Publishing validates
   ancestry and exact metadata/tag equality. Inspect the Release run and approve the
   environment; independently download artifacts and verify their attestations.

Branch protection must require `required-quality`, up-to-date branches, one approval,
resolved conversations, and linear history, including administrators. The shared
agent GitHub account cannot self-approve: an independent GitHub reviewer is required;
do not relax protection to work around this. Tag rules restrict creation, update and
deletion but explicitly allow the repository-admin role to bypass them; the shared
agent token has that role (`current_user_can_bypass: always`). This is **not** an
independent publishing approval boundary. The `pypi` environment has admin bypass
disabled and requires independent approval, even for a tag made with ruleset bypass.
Repository admins can still edit configuration and remain the ultimate trust root;
they must not disable these controls. Live configuration evidence belongs in the
issue handoff; inability to configure any control is a publication blocker.

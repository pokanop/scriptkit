# End-to-end release certification

This gate certifies the **provisional identity**, not a final name or PyPI launch.
No publishing credentials, Trusted Publisher, repository variable or release asset
is changed. POK-645 owns final naming/publication after the owner's decision.
Independent exact-head review and applicable QA are separate acceptance gates;
author-run tests do not replace them. Their verdicts and exact CI run/head belong
in the POK-631 handoff, not a self-issued approval in this document.

## Reproduce

From an environment provisioned with `.[dev]`, authenticated `gh`, git and Python:

```sh
python tools/certify_release.py --output certification
```

`docs/release-pins.json` records the exact reviewed GitHub tag, source commit,
seven asset digests and consumer revision. The runner:

1. Downloads those release assets with `gh`, rejects extra/missing files, verifies
   exact SHA256SUMS membership and all asset bytes, then verifies **every** GitHub
   attestation against repository, workflow, source/signing SHA and tag while
   rejecting self-hosted signers. Failures stop before executing release code.
2. Runs the documented bootstrap against the real public HTTPS wheel URL in a
   private Unicode/space-containing root; checks doctor, installs a second manager
   generation and exercises self-rollback. No PATH or user configuration is changed.
3. Extracts the already authenticated sdist (regular files only) and checks its
   metadata against the **historical** distribution/version/CLI/import in the pins.
   Installs the release wheel into a clean venv outside the source checkout and
   runs only that sdist's own tests/examples/installers using `--suite-root`, with
   imports checked against the venv prefix. Never runs head tests against an old
   release. Installed resources, POSIX/PowerShell bootstrap, registry consent, tool
   lifecycle, failures, interruption, recovery and AI fixture boundaries are exercised.
4. The separate 12-cell `runtime` matrix runs the **head** suite against the
   **candidate** wheel, including `test_authored_tool_release_journey`:
   installed manager → offline scaffold
   → handwritten handler → fixture-backed AI disclosure/review/apply → two built
   wheels → explicit catalog trust → install/run (answer 7) → update/run (42) →
   rollback/run (7) → uninstall. Real pip environments and launchers are used.
   Only the registry transport and AI provider are deterministic injected fixtures;
   no live model/key or production registry is required.
5. Rebuilds the verified sdist outside checkout, compares **every package file**
   against the release wheel (including template/contract/assistant resources),
   installs it into another clean venv and smokes help/version/resources.
6. Clones the exact original scripts consumer revision into a disposable directory,
   runs its transaction/migration/recovery suites and real generated-tool registration
   rehearsal, then builds the real pre-extraction distribution for the full
   install/update/interrupted-repair/ownership/source-rollback/used-clone rehearsal.
   Config and wrapper preservation are asserted. No user's installation is touched.
7. Benchmarks the installed release and a newly built current-head candidate on the
   **same runner**, retaining raw samples and enforcing regression budgets. Baseline
   workers import the pinned historical module and its sdist's installer fixture;
   candidate workers use the current pyproject entrypoint module and head fixture.
   The worker protocol is shared, not the implementation/suite or import identity.

`certification.json` is written even on failure (`complete: false`); only a true
completion plus green job represents a pass. JUnit `framework.xml` and `consumer.xml`,
`benchmark.json` and `candidate-benchmark.json` are uploaded by each of the three
required CI jobs. The existing 12-cell Python/OS runtime matrix still runs both
bare and Rich current-head installed-artifact tests. `required-quality` fails closed
if any matrix or prerequisite is skipped, cancelled or failed.

Network is required for GitHub verification, build-tool provisioning and original
consumer dependency acquisition. Authoring and the deterministic tool registry
journey are offline. This is clean-environment acceptance, not an air-gapped install
claim. All provider behavior is synthetic; real secrets/data never enter CI.

## Advancing pins without a release/merge cycle

The required gate has two independent inputs: the already published, authenticated
release in `release-pins.json` and the current candidate metadata/source. A normal
version bump or coordinated rename **leaves the historical pins unchanged**.
`distribution`, `module`, `command`, repository, tag and asset names describe the
old release, not head; the pins test permits `pins.version <= project.version`.
Do not reset version numbering during the rename without a separately reviewed
lineage policy. Head tests are never retroactively imposed on the old release.

POK-645 first changes candidate names/imports/CLI/repository and regenerates its
own templates/snapshots while retaining the old pins and pinned scripts consumer.
The candidate runtime matrix and the historical three-OS certification can both
pass **before** any renamed release exists. Benchmark imports/fixture paths are
explicitly selected per worker, so changing candidate imports cannot affect the
old worker. Never mechanically replace historical names or hashes in the pins.

After the candidate is reviewed, CI-green and merged, an authorized release owner
builds/signs a new immutable GitHub release using the normal release workflow.
Only then, in a reviewed follow-up change:

1. Download and independently verify every new asset's attestation against exact
   source/signing SHA, ref and workflow; verify the manifest and sdist metadata.
2. Advance the complete pins record atomically: historical identity fields, version,
   tag, commit, filenames and all seven digests. Never invent pins to unblock a PR,
   overwrite an old asset or disable certification. Keep old pins in Git history.
3. Advance `scripts_commit` only to a reviewed consumer compatible with those pins;
   its migration/rollback tests must still work. Coordinate renamed-consumer release
   constraints instead of silently running the old adapter against a new import.
4. Rerun three-OS certification, current candidate runtime tests, package/resource
   parity and performance comparison; update acquisition docs and rename inventories.
   Review any required historical-suite dependency changes explicitly.

This procedure does not enable PyPI or authorize a final name. Historical fixtures
are trusted, verified release code; live GitHub/PyPI/network availability remains a
CI dependency. Current-head features and failures are covered by current-head tests,
not claimed to have existed in an earlier release.

## Performance method and budgets

`tools/benchmark.py` records OS/architecture/Python, all wall-clock samples and
medians: five fresh-process interpreter/import/help samples, five fresh-project
render+preview+transaction generations, and five real pip installations of the
same tiny verified wheel. Baseline and candidate run in five interleaved pairs,
alternating which goes first; both environments are provisioned before measurement.
This avoids comparing a warmed-up release against a candidate immediately after
build/pip activity while Windows antivirus is still scanning. There is no automatic
retry-until-pass. Standalone `--benchmark` runs retain the original three-install
sampling method used by the checked-in Linux baseline. Installer time includes venv, ensurepip, hash/archive
validation, offline pip, smoke, receipt and activation; it excludes network and
large dependency downloads. Filesystem caches are warm. This is not a cold-boot,
GUI responsiveness, AI inference or large-model benchmark.

The checked-in [Linux baseline](performance-linux.json) was measured from the
verified 1.5.0 wheel on CPython 3.13.5 x86_64: interpreter ~5 ms, import ~25 ms,
help ~61 ms, generation ~5 ms, installer ~1.39 s. It is reproducible evidence, not
a universal target for Windows/macOS or a claim of improvement over another tool.
Each CI platform also retains its own release baseline beside candidate results.

For candidate versus release on the **same OS/Python runner**, every median must
stay below **2× baseline + 100 ms**, or **2× baseline + 2 s** for pip installation.
The multiplicative tolerance absorbs shared-runner CPU variance; the small additive
floor avoids failing millisecond-scale operations on scheduler noise, while the
installer allowance accounts for antivirus/venv process churn. Absolute hang/user
latency ceilings remain 1 s interpreter/import, 2 s help/generation and 60 s install.
The relative test is the regression gate, not these deliberately wider portability
ceilings. Investigate failures and fix regressions; do not silently increase budgets.
No major regression or unsupported speedup is claimed by this documentation-only
runtime release slice.

## Evidence limitations

See [support matrix](support.md) and [threat model](security-certification.md).
Three hosted OS families are not all architectures/filesystems. The original
six-tool GPU/service-dependent suite is not universally portable; this gate runs
the real `pluck` migration plus collection transaction and generated-tool flows,
not credentialed application behavior. Windows untrusted arguments must not pass
through `.cmd` wrappers. Runtime-only 1.3.0 compatibility remains covered separately.
Main merge protection remains the owner's accepted procedural control; this gate
does not claim to restore technical branch protection.

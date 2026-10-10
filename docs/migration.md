# Migration, recovery and rollback

There are three independent layers. Never infer that reverting one reverts all:

| Layer | Upgrade | Recovery / rollback |
| --- | --- | --- |
| Manager | Verified bootstrap or pinned `self-update` | Rerun verified bootstrap / `self-rollback` |
| Installed tool | `update NAMESPACE/TOOL@VERSION` | `recover TOOL` / `rollback TOOL` |
| Authored source | Preview/apply known template migration | Journal recovery / restore whole source snapshot |

Before migration, preserve config/data, old source revision, artifact hashes,
receipts and installation/bin locations. Stop writes where your tool requires it.
A venv is not a sandbox or a data backup. Rollback verifies retained generations;
modified/missing files fail closed. Uninstall intentionally retains generations
and user data. See [installation](installation.md) for safe manual space reclamation.

## Original pokanop/scripts collection

The collection is a **consumer adapter**, not another framework registry. Its
`scripts` CLI owns discovery, per-tool dependency snapshots and migration routing;
ScriptKit owns verified environments, receipts and activation. The consumer pinned
by [release-pins.json](release-pins.json) is version 1.2.0 of its distribution
(the historical `scripts --version` banner is 1.3.0); the framework is 1.5.0.
Do not confuse these independent versions.

Read the consumer's authoritative
[manager migration guide](https://github.com/pokanop/scripts/blob/68f12bc5b4baf845916a77e5e47dd0e5b6ab0da6/docs/manager-migration.md)
and [runtime extraction guide](https://github.com/pokanop/scripts/blob/68f12bc5b4baf845916a77e5e47dd0e5b6ab0da6/docs/runtime-migration.md).
With the consumer installer already updated at its existing install/bin paths:

```sh
scripts list
scripts migrate --dry-run
scripts migrate pluck
pluck --help
scripts update pluck
scripts rollback
```

A subset migration leaves unselected legacy tools working; a no-name migration
stages the v1 marker's selected set before committing routing. `scripts rollback`
restores the previous migration routing snapshot, **not** the source checkout.
Do not delete the old `venv`, immutable tool generations or transaction receipts
until the rollback window is explicitly over. Config/data must remain unchanged.

For source/runtime extraction rollback, restore the exact old collection source
AND its old distribution together; first remove the new runtime/distribution
ownership, then install the retained old wheel. Never leave overlapping old/new
owners of `scriptkit` files. The supported consumer installer detects/removes old
ownership before repairing framework files. `tests/rehearse_runtime_migration.py`
builds the real legacy wheel, tests interrupted uninstall/repair, old editable
ownership and used clones with orphaned bytecode, then reverts source and ownership
at the same paths while preserving config and wrappers.

Register a generated repository-style tool with `scripts register PROJECT`, then
`scripts install NAME --no-pull --no-path`. Build/resolve its dependencies under
the consumer's verified framework constraint. Uninstall before `scripts unregister
NAME`; unrelated tools, source and user state remain intact. Certification runs
this flow in a disposable clone, not against a user's installation.

The six original tools have different external-service/platform dependencies.
Transaction fixtures and the real `pluck` migration are not a claim that every
GPU model, credentialed provider or native application works on every OS. See
[support/evidence boundaries](support.md); no live credentials are used in CI.

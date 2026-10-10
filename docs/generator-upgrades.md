# Upgrade generated projects without losing manual work

Framework version and template version are different: framework **1.5.0** ships
template **2.0.0**, composed over the immutable v1 renderer. Only the named
v1→v2/text-lf-1 migration and v2 regeneration are supported. There is no arbitrary
executable migration/plugin hook.

1. Commit or back up the complete project, including hidden ownership files and
   manual sources. Record the old framework wheel/pin. Keep backup/context/proposal
   files outside the project.
2. Install the verified new framework into a separate author environment. Run
   `scriptkit template-upgrade PROJECT` to inspect the complete preview; no writes.
3. Run `scriptkit template-upgrade PROJECT --check` to detect generated drift.
   Modified generated files block publication. Reconcile them manually against the
   saved baseline; do not delete the manifest or invent new hashes to bypass CAS.
4. Apply with `scriptkit template-upgrade PROJECT --apply`. User-owned handlers,
   initializers, README, tests, CI and config examples remain intact. For old Git
   projects manually add LF rules for `/scaffold.json`, `/requirements.txt` and
   `/bin/*` to the preserved `.gitattributes`.
5. Run static `scriptkit validate PROJECT`, inspect diffs, then execute only the
   tests you explicitly trust in a disposable environment. Build/install the wheel
   outside its source tree and exercise help, doctor and your handlers.

Interrupted apply is not multi-file atomic visibility. Stop using the project,
then rerun `template-upgrade PROJECT --recover` with required inputs; the journal
rolls forward and refuses intervening edits. Do not edit half-written generated
files or delete the journal. For low-level generation/AI recovery see the
[generator](generator.md) and [AI](ai-proposals.md) guides.

Rollback authoring by restoring the **entire** saved project snapshot and old
author environment together, after resolving any active transaction. Tool
installation rollback is separate: `scriptkit rollback TOOL` restores a verified
retained tool generation, not the working tree. Manager rollback is separate again:
`scriptkit self-rollback`. See [migration](migration.md).

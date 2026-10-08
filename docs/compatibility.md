# Compatibility and versions

The initial standalone build is 1.3.0 to match the extracted runtime. Distribution
metadata uses the provisional name `pokanop-scriptkit`; the import remains
`scriptkit`. No PyPI availability or publication is implied. Python 3.11 is the
minimum. Rich is optional; requests is not required.

Public API comprises `scriptkit.__all__` and the documented runtime entry points.
The original exported callable signatures are checked against a golden snapshot,
and all twelve inherited source files remain unchanged. Return/exit semantics:

- `run_cli`: None, bool and non-int results mean 0; int results pass through.
- `CliError`: stderr diagnostic and 1; unexpected exceptions propagate.
- `KeyboardInterrupt`: cleanup callback attempted, ordinary cleanup failures
  swallowed, stderr diagnostic and 130. `SystemExit` propagates unchanged.
- `dispatch`: missing command help is 0; unknown handler is 1.
- New standalone argparse help/version exits 0; invalid arguments exit 2.

SemVer: patches preserve public behavior/signatures; minor versions add compatible
APIs; removal/signature breaks/default-behavior changes require a major version.
Deprecations must be documented in release notes and warn with
`DeprecationWarning` where appropriate for at least two minor releases before
removal in a major. Security exceptions require an explicit migration note,
not a silent compatibility change. No deprecated API is removed here.

## Evidence and limits

The baseline CI runs Python 3.11 on Linux, macOS and Windows. Each job runs the
library tests, checks the typed new CLI boundary, builds an sdist then its wheel,
and creates separate clean bare/Rich environments. It copies tests outside the
checkout, verifies installed import location, absence of requests, real process
exit codes and both console/module entry points. The POSIX signal regression
is intentionally skipped on Windows; this is not evidence of Windows console
control-event handling. The Rich-specific rendering test skips only in the bare
variant. Broader Python versions, release provenance and expanded quality gates
are POK-616, not claims of this foundation.

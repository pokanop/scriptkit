# Contributing

Use Python 3.11 or newer and an isolated environment. From the repository root:

```sh
python -m pip install -e '.[dev,rich]'
python -m pytest
python -m mypy
python -m build
python tools/check_artifact.py dist/pokanop_scriptkit-1.4.0-py3-none-any.whl
python tools/check_artifact.py dist/pokanop_scriptkit-1.4.0-py3-none-any.whl --rich
```

Build tooling/test dependencies need package-index access; runtime smoke operations
are local. Tests do not require provider keys. Never commit credentials or local
venvs. Test the installed wheel, not only editable imports. New runtime code must
include success/failure/interruption tests as applicable; new code should achieve
at least 90% changed-line coverage and cover critical recovery/security branches.
CI enforces 90% changed-line coverage across the framework, a 12-cell platform/Python
matrix, static checks, dependency auditing, and clean release rehearsals. See the
[quality and release contract](docs/quality-and-release.md) for required gates,
explicit legacy typing/formatting debt, provenance, and owner publishing setup.

Keep typed boundaries narrow and inject argv/IO/platform/provider dependencies.
Follow [ownership](docs/architecture.md) and [SemVer](docs/compatibility.md).
Do not update the API snapshot simply to make a breaking change pass. Explain
intentional differences in the PR, preserve MIT notices, and avoid consumer-only
dependencies. No fake manager/generator implementations in the composition root.

Submit a focused PR with test evidence. Rebase on main (never merge main into
feature branches), require current-head green CI and independent review before
merging. Do not publish artifacts while package/brand naming remains unresolved.

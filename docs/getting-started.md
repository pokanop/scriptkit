# Start from a verified GitHub release

ScriptKit is a working name; `pokanop-scriptkit` is provisional metadata, not a
PyPI launch. The supported acquisition channel is the immutable **v1.5.0 GitHub
release**, not `pip install pokanop-scriptkit` from an index. Final naming and
publication are POK-645, gated on the owner's explicit decision.

## Prerequisites and verification (all systems)

Install Python 3.11–3.14 with `venv`/`ensurepip` and the GitHub CLI. Use a private
installation directory. No administrator privileges or PATH modification are
needed. On Linux/macOS use `python3`; on Windows use your Python executable
(`python` below). Commands below are separate commands, usable in PowerShell too.
Authenticate `gh` if required by the repository/attestation API.

```sh
gh release download v1.5.0 --repo pokanop/scriptkit --dir release
```

Review the exact source/tag/asset hashes in [release-pins.json](release-pins.json).
For **each** downloaded asset, verify its SHA-256 and attestation before executing
anything. This example verifies the bootstrap; repeat with the wheel, sdist,
`install.sh`, `install.ps1`, `SHA256SUMS` and `build.json`:

```sh
gh attestation verify release/bootstrap.py --repo pokanop/scriptkit --signer-workflow pokanop/scriptkit/.github/workflows/release.yml --source-digest 96714be2479b4b4b1b6207a4d8db66c3aa07ceea --signer-digest 96714be2479b4b4b1b6207a4d8db66c3aa07ceea --source-ref refs/tags/v1.5.0 --deny-self-hosted-runners
python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('release/bootstrap.py').read_bytes()).hexdigest())"
```

Expected bootstrap SHA-256:
`45ccff104edaa5e6dedbd6c7c5e1b34d4d70c256fe68ef161e00eb6500645607`.
A checksum fetched from the same server alone is not independent authentication.
The [certification runner](certification.md) automates all seven verifications.

## Bootstrap and inspect

After verification, inspect `bootstrap.py`, then run this same command on any OS
(replace `PRIVATE_ROOT` with an absolute private directory):

```sh
python -I release/bootstrap.py --wheel https://github.com/pokanop/scriptkit/releases/download/v1.5.0/pokanop_scriptkit-1.5.0-py3-none-any.whl --sha256 89307e39af78f0c92d7002e60b30cf10e5015c35aaa5bc67b57b547822bdc75b --version 1.5.0 --root PRIVATE_ROOT
```

Use `PRIVATE_ROOT/bin/scriptkit` on Linux/macOS, or
`PRIVATE_ROOT/bin/scriptkit.cmd` on Windows. Substitute that full executable path
for `scriptkit` below. Do not run the runtime-only 1.3.0 wheel as a manager.
For shell/PowerShell wrappers, explicit PATH integration and repair see
[bootstrap](bootstrap.md).

```sh
scriptkit --version
scriptkit doctor
scriptkit --json registry list
```

No tool is installed yet. Obtain `registry.json` from a trusted publisher after
independent verification of its catalog pin and origin. There is no implicit
community registry or arbitrary PyPI fallback. The following are placeholders
for **your publisher's** namespace, exact versions and HTTPS origin:

```sh
scriptkit registry add registry.json --trust-origin https://tools.example.org/releases/
scriptkit catalog example
scriptkit install example/tool@1.0.0 --dry-run
scriptkit install example/tool@1.0.0
tool --help
scriptkit update example/tool@1.0.1
scriptkit rollback tool
scriptkit uninstall tool
```

Tool code is trusted executable Python: a venv is **not** a sandbox. Uninstall
retains user data and generation receipts. Offline installs require a complete,
reverified cache and unexpired trust. See [registry authoring](registry-author.md)
and [migration/rollback](migration.md) before adopting existing tools.

## Create a tool without AI or network

Scaffold generation uses bundled resources only. To run authoring commands via
`python`, first install the **verified local wheel** into a separate author venv:

On Windows substitute `author-env/Scripts/python.exe` for every
`author-env/bin/python` below (no activation is needed):

```sh
python -m venv author-env
author-env/bin/python -m pip install --no-index release/pokanop_scriptkit-1.5.0-py3-none-any.whl
author-env/bin/python -c "from pathlib import Path; from scriptkit.contracts import ToolSpec,resource_text; from dataclasses import replace; Path('tool-spec.json').write_text(replace(ToolSpec.from_json(resource_text('ToolSpec.example.json')),entrypoint='demo.cli:main').canonical_json(),encoding='utf-8')"
mkdir demo
author-env/bin/python -m scriptkit new-tool demo --spec tool-spec.json --apply
author-env/bin/python -m scriptkit validate demo
```

The example platform list is Linux x86_64; explicitly set `platforms` and
`python` in your spec to the systems you support before packaging. The command
writes UTF-8 explicitly, including on Windows PowerShell 5.1 where native stdout
redirection would otherwise create UTF-16.
Edit `demo/src/demo/_handlers.py`, not generated launchers. For example:

```python
def run(command, arguments):
    return {"target": arguments["target"], "inspected": True}
```

Build with your separately provisioned build tools (`python -m build demo`),
then install the generated wheel **alongside the verified framework wheel** using
`pip install --no-index FRAMEWORK_WHEEL TOOL_WHEEL`. Generation is offline; initial
provisioning of build dependencies need not be. Run the tool with
`example-tool --json inspect sample` and `example-tool doctor`.

Continue with [manual/collection scaffolds](scaffolds.md),
[generator upgrades](generator-upgrades.md), [conformance and agent guidance](conformance.md),
and optional [BYOK/privacy and fixture-backed AI](ai-proposals.md). No API key,
provider, model download or network access is required for manual authoring.

# Release support and evidence matrix

| Surface | Supported / tested contract |
| --- | --- |
| Framework 1.5.0 | CPython 3.11, 3.12, 3.13, 3.14; Linux, macOS, Windows |
| Required runtime matrix | GitHub-hosted ubuntu-latest, macos-latest, windows-latest × all four Python versions |
| GitHub artifact certification | All three hosted OS runners, Python 3.13; signed/pinned wheel and sdist outside source checkout |
| Unix bootstrap | POSIX shell plus separately installed Python with venv/ensurepip |
| Windows bootstrap | Windows PowerShell 5.1 and PowerShell 7; NTFS/private ACLs; no automatic policy change |
| Mandatory dependencies | None; pip/venv supplied by Python for installation |
| Optional display | Rich >=13.9.4,<15; bare and Rich installed-wheel suites, plus lower-bound CI |
| Optional AI | stdlib OpenAI/Ollama adapters; optional keyring >=25.7,<26; fixture-backed CI only |
| Tool installation | Verified wheels/Python-only legacy bundles; pip default; uv only if explicitly provisioned |
| Filesystems | Local private paths; process-interruption recovery, not universal power-loss/network-FS durability |
| Distribution channel | Verified GitHub v1.5.0 release; no PyPI publication under provisional identity |

Python 3.10, untested interpreters, mobile platforms, universal CPU coverage,
network filesystems and privileged/system-package automation are not promised.
The platform matrix is evidence for the hosted runner architectures recorded in
benchmark reports, not every OS version/architecture combination. Tool specs and
locks must narrow support to their own tested targets.

The original scripts adapter's migration, rollback and generated-tool lifecycle
run on all three systems. The real legacy/`pluck` flow executes without private
service credentials. The six-tool heavy-dependency Linux rehearsal belongs to
POK-629; GPU/model downloads and real provider/account behavior are not repeated
by this release gate and are not universal cross-platform guarantees.

Support is source-based: report reproducible defects through the repository issue
tracker with OS/Python, exact artifact SHA, command, sanitized diagnostics and
whether receipt verification/recovery succeeded. Never include credentials,
private records or raw AI context. Security reports follow [SECURITY.md](../SECURITY.md).
No commercial SLA or long-term maintenance of arbitrary historical generations is
claimed. Retain verified rollback inputs locally; do not overwrite release assets.

Contributors must follow [CONTRIBUTING.md](../CONTRIBUTING.md), independent exact-head
review, all required CI and applicable user-facing QA. Main gating is currently
owner-accepted **procedure-only**; release-environment approvals remain owner-managed.
This is not technically enforced branch protection. See
[security certification](security-certification.md) for the accepted residual risk.

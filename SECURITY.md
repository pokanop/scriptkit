# Security

Only the current main branch receives fixes; no released-version support
commitment exists yet. GitHub artifacts are published; PyPI publication is
separately gated and must not be inferred from the package name.

Report suspected vulnerabilities using GitHub's enabled
[private vulnerability reporting](https://github.com/pokanop/scriptkit/security/advisories/new).
Do not put credentials, exploit payloads containing personal data or unpublished
details in public issues. Include affected revision, minimal reproduction and impact.
There is no promised response SLA or bounty program.

## Trust model

See [the executable security assessment](docs/security-certification.md) for
assets, adversaries, positive/negative test evidence and residual risks across
registries, bootstrap, installation, config and AI. A passing test suite is not
a guarantee that arbitrary packages or model-generated code are safe.

- Runtime and installed tools are **not sandboxes**. Venvs isolate dependencies,
  not filesystem, network, environment credentials or OS privileges. Install and
  smoke-check only code you trust, as an unprivileged user. Use an OS sandbox or
  disposable account for untrusted code. Package installation can execute code.
- Registry consent binds an exact HTTPS origin and catalog digest, not publisher
  benevolence. No automatic namespace replacement, latest-version fallback or
  implicit trust from cache. Offline operation still checks pins and expiry.
- Installer/config/cache roots and their parents must be private, user-owned
  local directories. Receipts and hashes detect corruption; they are not signed
  against an attacker who can rewrite the entire private state.
- AI is optional and never implicitly paid. Inspect selected context and the
  exact provider/model/destination before approving transmission; inspect the
  diff before applying. Static checks do not prove generated code harmless.
  Secret detection is defense in depth, not a complete data-loss-prevention system.
- Prefer argv lists and `shell=False`. Legacy `Config` may hold caller secrets;
  consumers own permissions, redaction and storage choices. New layered config
  supports explicit secret references; never log resolved keys.

CI uses synthetic credentials and read-only permissions for PR tests. Release
provenance/publication jobs have narrowly scoped additional permissions and
must pass the same quality gates; dispatch alone cannot publish to PyPI.
Independent review on the exact head remains a procedural delivery requirement,
separate from test success. Currently `main` has no branch protection/ruleset;
CI success and PR approval are not technically enforced at merge or direct-push
time. The admin identity agents use can also bypass the tag ruleset. These are
unresolved administrative risks requiring the owner's enforcement or explicit
acceptance, not security guarantees. PyPI's environment blocks admin bypass and
self-review, but its sole reviewer is that same identity, so agent-pushed tags
currently have no eligible approver. See the assessment for the owner decision.

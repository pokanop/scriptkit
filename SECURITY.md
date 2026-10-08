# Security

This foundation is not yet a published release. Only the current main branch
receives fixes; no released-version support commitment exists yet.

Report suspected vulnerabilities using GitHub's enabled
[private vulnerability reporting](https://github.com/pokanop/scriptkit/security/advisories/new).
Do not put credentials, exploit payloads containing personal data or unpublished
details in public issues. Include affected revision, minimal reproduction and impact.
There is no promised response SLA or bounty program.

The runtime is not a sandbox. `proc.run` executes caller-supplied commands;
prefer argv lists and the default `shell=False`, and do not pass untrusted shell
strings. Config files may contain caller secrets: consumers own permissions,
redaction and storage choices. Never log keys. Rich is an optional presentation
dependency, not a trust boundary. Network registry, manager transactions and AI
provider security are not implemented and must not be inferred from the CLI.

CI uses read-only repository permissions and does not publish packages or handle
release credentials. Namespace/brand review and secure release gates must land
before any public package upload. Please report naming confusion separately from
security vulnerabilities.

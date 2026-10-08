# Reviewable AI proposals (v1)

AI is optional. `scriptkit.generator` does not import AI code; manual/offline generation remains byte-identical without a provider. Inference is **nondeterministic**. Reproducibility begins at saved, approved inputs, not at a prompt or model name.

This release provides a typed provider port, offline `FakeProvider` contract harness, optional OpenAI/Ollama HTTP adapters and a file-based review CLI. It never sends context automatically, installs dependencies or runs proposed code/tests. There is deliberately **no execution option**, including no host-based opt-in runner. Use an independently reviewed disposable sandbox (no credentials, network disabled) if you later choose to execute untrusted code; a venv or temporary directory alone is not a sandbox.

## Select, preview, propose, review, apply

Start with a v2 `scriptkit new-tool` project. Select individual extension files; no automatic repository/home-directory context collection:

```sh
python -m scriptkit.ai context ./demo src/demo/_handlers.py tests/test_ai_handler.py examples/ai_usage.md > context.json
```

Inspect **all** of `context.json` before sharing it. It contains the ToolSpec and exactly the selected file contents (null for a new file). There is no reliable general secret detector: remove credentials or personal information embedded in otherwise allowed descriptions/code before disclosure. Context is untrusted data, not instructions; providers must preserve that distinction.

The supported writable extensions are the entrypoint package's `_handlers.py`, `tests/test_ai_<name>.py`, and `examples/ai_<name>.md` (lowercase letters, digits and underscores). Tests/examples may be new. All must be explicitly selected. Other user modules remain manual-authoring territory. Generated launchers, ownership manifests, bootstrap/install/trust settings, secrets/config, CI files, dependency metadata and lockfiles cannot be direct patch targets.

The provider interface is `Provider.propose(Context, max_response_bytes=...) -> bytes`. `request(provider, context, approved_context_hash=context.identity)` requires approval of that exact preview before invoking the adapter. An adapter must enforce the response budget while receiving data, not merely after buffering an unlimited stream, and must not perform tool calls or execute returned instructions. No credentials enter the context contract. The adapter implementation itself is trusted application code.

`Proposal.json_schema()` exposes the strict v1 response schema. Every field is required; unknown/duplicate keys, forbidden control characters and invalid types are rejected. File-content fields preserve both LF and CRLF exactly (including in base hashes); bare carriage returns remain forbidden. Other text fields keep the existing LF/tab-only control-character policy. An example response shape (replace hashes and command name with actual values):

```json
{
  "schema_version": 1,
  "context_hash": "<Context.identity>",
  "spec_delta": {
    "description": "Describe the tool clearly",
    "command_help": [{"name": "hello", "help": "Explain this existing command"}]
  },
  "patches": [{
    "path": "src/demo/_handlers.py",
    "base_hash": "<SHA-256 of exact original UTF-8 bytes>",
    "content": "def run(command, values):\n    return {'answer': 42}\n"
  }],
  "dependency_suggestions": []
}
```

`description: null` leaves the description unchanged; empty arrays mean no changes. `base_hash: null` asserts that a selected target does not exist. Patches replace full files, not fuzzy hunks. Spec deltas may only edit the tool description and existing command help: not names, arguments, versions, platforms, entrypoints or trust controls. The trusted renderer derives any resulting generated changes, including its own manifest; the provider cannot supply ownership metadata.

```sh
python -m scriptkit.ai review ./demo context.json proposal.json > review.json
# Inspect every unified diff and dependency suggestion, then copy approval_hash:
python -m scriptkit.ai apply ./demo context.json proposal.json --approve <approval_hash>
```

Review is read-only against the project. It builds a data-only scratch snapshot and uses the existing static conformance checker, including syntax/import/output checks for proposed tests. Tests may import the scaffold's `pytest` runner; that permission never applies to runtime modules. It never imports that snapshot. Undeclared statically visible imports fail even when listed as dependency suggestions. Suggestions are informational: a human must separately review/edit dependency metadata. Sanctioned additive dependency/import-alias metadata extensions are preserved and hash-bound for handler-only or command-help proposals, provided rendering leaves the recorded metadata baseline unchanged. A description change that would rewrite such metadata still requires manual reconciliation. Other generated drift blocks AI application rather than being overwritten. Non-source build/desktop artifacts under `src/` are ignored; symlinks/reparse points remain forbidden.

Apply recomputes review, checks approval against the envelope/diffs and checked base bytes, then uses the generator's locked, journaled transaction. Any malformed, oversized, forbidden, stale or statically invalid proposal fails before project writes. Context permits 32 files, 64 KiB per file and 512 KiB serialized total; responses permit 32 patches and 256 KiB serialized total. Static snapshots are limited to 256 source/base files and 2 MiB. Filesystem concurrency assumes a trusted project directory, not hostile concurrent writers.

If interrupted during publication, do not execute the project until `python -m scriptkit.generator PROJECT/tool.json PROJECT --recover` completes (the existing generator recovery command). Multi-file atomic visibility is not claimed. The persisted journal permits deterministic roll-forward and refuses intervening edits. Keep original `context.json`, `proposal.json`, the reviewed hash and the original project revision outside the project: replay on the same base produces identical approved files. Afterwards, normal regeneration from saved `tool.json` preserves the user-owned handler/tests/examples.

## Guided BYOK authoring (optional)

A complete offline-first flow (shell redirections work in PowerShell too):

```sh
# Prepare a ToolSpec offline; the example must use the generated package convention.
python -c "from scriptkit.contracts import ToolSpec, resource_text; from dataclasses import replace; print(replace(ToolSpec.from_json(resource_text('ToolSpec.example.json')), entrypoint='demo.cli:main').canonical_json())" > demo-spec.json
mkdir demo
scriptkit new-tool demo --spec demo-spec.json --apply
scriptkit validate demo
python -m scriptkit.ai context demo src/demo/_handlers.py > context.json
# Preview only: no key lookup or request. Choose a model already installed in Ollama.
python -m scriptkit.ai propose demo src/demo/_handlers.py --provider ollama --model YOUR_LOCAL_MODEL --goal 'Implement the hello handler' > disclosure.json
# Inspect the full disclosure (including selected context), copy its approval_hash,
# then repeat EXACTLY the same arguments and add approval:
python -m scriptkit.ai propose demo src/demo/_handlers.py --provider ollama --model YOUR_LOCAL_MODEL --goal 'Implement the hello handler' --approve-disclosure EXACT_DISCLOSURE_HASH > proposal.json
python -m scriptkit.ai review demo context.json proposal.json > review.json
# Inspect every diff, copy review approval_hash; neither operation runs proposed code.
python -m scriptkit.ai apply demo context.json proposal.json --approve EXACT_REVIEW_HASH
scriptkit validate demo
```

Keep review/context/proposal files outside the scaffold. If a proposal fails static
checks, edit manually or explicitly request another proposal; there is no repair
loop, fallback provider, dependency installation, publishing or commit operation.
Offline/manual scaffold generation is unchanged if AI is never imported.

For OpenAI, replace `--provider ollama --model ...` with `--provider openai --model
YOUR_OPENAI_MODEL` in BOTH preview and approved commands. Select a chat-completions
model supporting JSON mode and `max_completion_tokens`. Provide `OPENAI_API_KEY`
through your shell's secret injection facilities, never a command argument or
project file. Alternatively install the optional `pokanop-scriptkit[ai-keyring]`
extra yourself and store a password in your OS keyring under service `scriptkit.ai`,
username `openai`; pass `--keyring` to opt into lookup. Environment takes precedence.
No package is installed automatically. Missing keys/keyring backends fail with
manual-edit guidance, without project changes. There are no provider SDK requirements:
both adapters use Python's standard-library HTTP client, imported only by AI.

OpenAI's destination is fixed to `https://api.openai.com/v1/chat/completions`.
Ollama defaults to `http://127.0.0.1:11434/api/chat`; `--endpoint` accepts only literal
IPv4/IPv6 loopback HTTP URLs with an explicit port and `/api/chat` path (no userinfo,
query or fragment). Local endpoints never load or receive cloud credentials.
Proxies, redirects, cookies and automatic authentication discovery are disabled.
The endpoint process itself is trusted; ScriptKit cannot stop it forwarding data.
No automatic model downloads are requested. Context selection never crawls the
repository/home directory and excludes `.env`, configuration and arbitrary files.
A common-secret check inspects raw file/ToolSpec strings and the goal for PEM keys,
`sk-` prefixes and quoted credential assignments of at least eight non-whitespace
characters. Ordinary variable lookups and credential-related instructions are allowed.
This is defense in depth, NOT a reliable scanner: inspect every selected byte,
including ToolSpec descriptions, before approval.

`--max-requests` defaults to 1 (hard ceiling 4), `--retries` to 0 (ceiling 3).
Only HTTP 429/503 are retryable and each retry consumes BOTH budgets. Retries wait
1, 2, then 4 seconds (at most 7 total), interruptible with Ctrl-C; exhausted budgets
stop before waiting or sending. This fixed bounded backoff does not interpret
`Retry-After`; long server-side rate-limit windows may still require a later explicit
invocation. There is no unbounded backoff or paid fallback. `--max-tokens` defaults to 32768 (ceiling 262144)
and reserves serialized request UTF-8 bytes + 1024 framing allowance + the entire
output allowance on each attempt, even failures. This intentionally conservative
estimate is not a price guarantee or account-wide quota. `--max-output-tokens`
defaults to 4096 (ceiling 16384) and is sent to the provider as its generation-token
cap. Local response validation independently enforces the 256 KiB proposal byte
contract, not a token-count-as-bytes limit. Providers must be trusted to honor their
token limits; byte checks cannot guarantee spending. Large contexts may require a
deliberate budget increase or smaller selection. Limits apply per CLI invocation/
provider instance, not across invocations.
`--timeout` sets the non-streaming response wait (default 120 seconds, ceiling 600).
Because no response bytes arrive during generation, it must cover model loading
and inference as well as network latency. Set it higher for slow/local models.
Underlying socket operations use this timeout; it is not an end-to-end deadline
across retries. Ctrl-C closes the connection or interrupts backoff, exits 130 and
writes no proposal/project files; cancellation/timeouts cannot reverse a charge
already incurred.

Preview hashes bind context, goal, model, destination and all limits. Stdout contains
only versioned JSON (preview/proposal/review); diagnostics go to stderr, no animation.
Provider error bodies, transport exception text and credentials are never logged.
A provider echo of the loaded key is rejected, not persisted. Proposal text still
requires human inspection for other sensitive content. Shell redirection may create
an empty output file on failure, but ScriptKit never modifies the scaffold until
explicit `apply`. Routine CI uses synthetic wire fixtures, not live keys or models.

## Trust limits

**Static checks are not a security proof.** Python can hide dependencies, use dynamic imports, perform destructive operations or exfiltrate data when eventually executed. Prompt-injection text is not reliably recognizable by a string scanner. The tested defense is structural: it cannot grant new paths, fields, tool calls, dependency installation or execution authority. All proposal text fields reject Unicode format characters (category `Cf`, including bidi overrides/isolates, zero-width format characters and BOM) to prevent visually reordered or hidden diff content. Existing context can still contain these characters for explicit remediation; this is not a general homoglyph or malicious-code detector. Human review remains mandatory, including review of allowed Python and Markdown contents. Approval hashes identify exact data; they are not signatures, authenticated user identities or proof of safety. Restrict access to your project, provider adapter and approval channel accordingly.

The runtime, manager, registry and generator have no provider dependency. AI uses the generator's deterministic render/preview/transaction primitives and conformance's static validator rather than another file writer, dependency resolver or execution engine.

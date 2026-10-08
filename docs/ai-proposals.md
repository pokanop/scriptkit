# Reviewable AI proposals (v1)

AI is optional. `scriptkit.generator` does not import AI code; manual/offline generation remains byte-identical without a provider. Inference is **nondeterministic**. Reproducibility begins at saved, approved inputs, not at a prompt or model name.

This release provides a typed provider port, offline `FakeProvider` contract harness and file-based review CLI. It does not bundle a hosted provider, send context automatically, install dependencies or run proposed code/tests. There is deliberately **no execution option**, including no host-based opt-in runner. Use an independently reviewed disposable sandbox (no credentials, network disabled) if you later choose to execute untrusted code; a venv or temporary directory alone is not a sandbox.

## Select, preview, propose, review, apply

Start with a v2 `scriptkit new-tool` project. Select individual extension files; no automatic repository/home-directory context collection:

```sh
python -m scriptkit.ai context ./demo src/demo/_handlers.py tests/test_ai_handler.py examples/ai_usage.md > context.json
```

Inspect **all** of `context.json` before sharing it. It contains the ToolSpec and exactly the selected file contents (null for a new file). There is no reliable general secret detector: remove credentials or personal information embedded in otherwise allowed descriptions/code before disclosure. Context is untrusted data, not instructions; providers must preserve that distinction.

The supported writable extensions are the entrypoint package's `_handlers.py`, `tests/test_ai_<name>.py`, and `examples/ai_<name>.md` (lowercase letters, digits and underscores). Tests/examples may be new. All must be explicitly selected. Other user modules remain manual-authoring territory. Generated launchers, ownership manifests, bootstrap/install/trust settings, secrets/config, CI files, dependency metadata and lockfiles cannot be direct patch targets.

The provider interface is `Provider.propose(Context, max_response_bytes=...) -> bytes`. `request(provider, context, approved_context_hash=context.identity)` requires approval of that exact preview before invoking the adapter. An adapter must enforce the response budget while receiving data, not merely after buffering an unlimited stream, and must not perform tool calls or execute returned instructions. No credentials enter the context contract. The adapter implementation itself is trusted application code.

`Proposal.json_schema()` exposes the strict v1 response schema. Every field is required; unknown/duplicate keys, control characters and invalid types are rejected. An example response shape (replace hashes and command name with actual values):

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

Review is read-only against the project. It builds a data-only scratch snapshot and uses the existing static conformance checker, including syntax/import/output checks for proposed tests. It never imports that snapshot. Undeclared statically visible imports fail even when listed as dependency suggestions. Suggestions are informational: a human must separately review/edit dependency metadata and resolve generator ownership conflicts. Existing generated drift (including handwritten dependency metadata extensions) blocks AI application rather than being overwritten.

Apply recomputes review, checks approval against the envelope/diffs and checked base bytes, then uses the generator's locked, journaled transaction. Any malformed, oversized, forbidden, stale or statically invalid proposal fails before project writes. Context permits 32 files, 64 KiB per file and 512 KiB serialized total; responses permit 32 patches and 256 KiB serialized total. Static snapshots are limited to 256 source/base files and 2 MiB. Filesystem concurrency assumes a trusted project directory, not hostile concurrent writers.

If interrupted during publication, do not execute the project until `python -m scriptkit.generator PROJECT/tool.json PROJECT --recover` completes (the existing generator recovery command). Multi-file atomic visibility is not claimed. The persisted journal permits deterministic roll-forward and refuses intervening edits. Keep original `context.json`, `proposal.json`, the reviewed hash and the original project revision outside the project: replay on the same base produces identical approved files. Afterwards, normal regeneration from saved `tool.json` preserves the user-owned handler/tests/examples.

## Trust limits

**Static checks are not a security proof.** Python can hide dependencies, use dynamic imports, perform destructive operations or exfiltrate data when eventually executed. Prompt-injection text is not reliably recognizable by a string scanner. The tested defense is structural: it cannot grant new paths, fields, tool calls, dependency installation or execution authority. Human review remains mandatory, including review of allowed Python and Markdown contents. Approval hashes identify exact data; they are not signatures, authenticated user identities or proof of safety. Restrict access to your project, provider adapter and approval channel accordingly.

The runtime, manager, registry and generator have no provider dependency. AI uses the generator's deterministic render/preview/transaction primitives and conformance's static validator rather than another file writer, dependency resolver or execution engine.

"""Cross-boundary proposal substitution and private-data canaries."""

from dataclasses import replace
import json
import traceback

import pytest

from scriptkit.ai import apply, review, select
from scriptkit.ai.contracts import MAX_RESPONSE
from scriptkit.ai.providers import HTTPProvider, ProviderError
from test_ai import HANDLER, project as project, proposal, selected, snapshot


def test_approved_review_cannot_authorize_substituted_model_output(project):
    context = selected(project)
    original = proposal(context)
    checked = review(project, context, original)
    changed = replace(
        original,
        patches=tuple(
            replace(patch, content=patch.content.replace("42", "43")) for patch in original.patches
        ),
    )
    before = snapshot(project)
    with pytest.raises(ValueError, match="approval"):
        apply(project, context, changed, approved_review_hash=checked.approval_hash)
    assert snapshot(project) == before
    apply(project, context, original, approved_review_hash=checked.approval_hash)
    assert "42" in (project / HANDLER).read_text()
    with pytest.raises(ValueError):
        apply(project, context, original, approved_review_hash=checked.approval_hash)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_private_files_and_ambient_keys_never_enter_wire_context(project, monkeypatch, provider):
    secret = "synthetic-private-file-canary-630"
    key = "synthetic-cloud-key-canary-630"
    (project / ".env").write_text(secret)
    private_home = project.with_name(project.name + "-private-home")
    private_home.mkdir()
    (private_home / "private.txt").write_text(secret)
    monkeypatch.setenv("HOME", str(private_home))
    monkeypatch.setenv("USERPROFILE", str(private_home))
    monkeypatch.setenv("OPENAI_API_KEY", key)
    context = select(project, (HANDLER,))
    calls = []

    def transport(url, body, headers, timeout, maximum):
        calls.append((body, headers))
        assert secret not in body.decode() and key not in body.decode()
        assert (headers.get("Authorization") == "Bearer " + key) == (provider == "openai")
        # A malicious error response tries to echo BOTH private data and credentials.
        return 401, (secret + key).encode()

    client = HTTPProvider(provider, "synthetic", "Improve help", transport=transport)
    before = snapshot(project)
    assert secret not in json.dumps(client.preview(context))
    with pytest.raises(ProviderError) as caught:
        client.propose(context, max_response_bytes=MAX_RESPONSE)
    diagnostic = "".join(traceback.format_exception(caught.value))
    assert secret not in diagnostic and key not in diagnostic
    assert len(calls) == 1
    assert snapshot(project) == before


def test_keyring_backend_failure_is_sanitized(project, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sentinel = "synthetic-keyring-secret-630"

    def broken_import(name):
        assert name == "keyring"
        raise RuntimeError(sentinel)

    monkeypatch.setattr("scriptkit.ai.providers.importlib.import_module", broken_import)
    client = HTTPProvider("openai", "synthetic", "Improve help", use_keyring=True)
    before = snapshot(project)
    with pytest.raises(ProviderError, match="keyring unavailable") as caught:
        client.propose(select(project, (HANDLER,)), max_response_bytes=MAX_RESPONSE)
    assert sentinel not in "".join(traceback.format_exception(caught.value))
    assert snapshot(project) == before

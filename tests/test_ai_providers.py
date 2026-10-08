"""Synthetic wire recordings only: no live keys, network inference or paid CI."""

from dataclasses import replace
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scriptkit.ai import select
from scriptkit.ai.__main__ import main
from scriptkit.ai.contracts import MAX_RESPONSE, Patch, Proposal, SpecDelta
from scriptkit.ai.providers import (
    HTTPProvider,
    Limits,
    ProviderError,
    credential,
    destination,
    http_post,
)
from scriptkit.contracts import ToolSpec, resource_text
from scriptkit.generator import apply as generate, preview
from scriptkit.generator.scaffolds import tool

FIXTURES = json.loads((Path(__file__).parent / "fixtures/ai-providers.json").read_text())


@pytest.fixture
def project(tmp_path):
    spec = replace(
        ToolSpec.from_json(resource_text("ToolSpec.example.json")), entrypoint="demo.cli:main"
    )
    generate(tmp_path, preview(tmp_path, tool(spec)))
    return tmp_path


def response(context, shape):
    proposal = Proposal(1, context.identity, SpecDelta("Improved description", ()), (), ())
    return (
        json.dumps(FIXTURES[shape])
        .replace("PROPOSAL", proposal.canonical_json().replace('"', '\\"'))
        .encode()
    )


class Recording:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        return result


def provider(context, shape="openai", **kwargs):
    return HTTPProvider(
        shape,
        "synthetic-model",
        "Improve description",
        transport=Recording((200, response(context, shape))),
        load_key=lambda **_: "synthetic-test-key",
        **kwargs,
    )


@pytest.mark.parametrize("shape", ["openai", "ollama"])
def test_two_independent_wire_shapes(project, shape):
    context = select(project, ("src/demo/_handlers.py",))
    client = provider(context, shape)
    plan = client.preview(context)
    user_message = json.loads(plan["messages"][1]["content"])
    assert user_message["base_hashes"] == {"src/demo/_handlers.py": context.files[0].base_hash}
    assert not client.transport.calls
    assert "synthetic-test-key" not in json.dumps(plan) + repr(client)
    proposed = Proposal.from_json(client.propose(context, max_response_bytes=MAX_RESPONSE).decode())
    assert proposed.context_hash == context.identity
    url, body, headers, timeout, maximum = client.transport.calls[0]
    payload = json.loads(body)
    assert payload["model"] == "synthetic-model" and not payload["stream"]
    assert timeout == 30 and maximum == MAX_RESPONSE * 8
    if shape == "openai":
        assert headers["Authorization"] == "Bearer synthetic-test-key"
        assert payload["max_completion_tokens"] == 4096
    else:
        assert "Authorization" not in headers
        assert payload["options"]["num_predict"] == 4096
        assert url.startswith("http://127.0.0.1:")


@pytest.mark.parametrize(
    "event", ["auth", "rate_limit", "unavailable", "invalid", "timeout", "cancel"]
)
def test_recorded_failures_are_redacted(project, event):
    context = select(project, ())
    client = provider(context)
    fixture = FIXTURES[event]
    result = (
        {"TimeoutError": TimeoutError, "KeyboardInterrupt": KeyboardInterrupt}[
            fixture["exception"]
        ]("sensitive-key-and-context")
        if "exception" in fixture
        else (fixture["status"], fixture["body"].encode())
    )
    client.transport = Recording(result)
    with pytest.raises(KeyboardInterrupt if event == "cancel" else ProviderError) as caught:
        client.propose(context, max_response_bytes=MAX_RESPONSE)
    if event != "cancel":
        assert "sensitive" not in str(caught.value)
    assert len(client.transport.calls) == 1


def test_budget_retry_and_repeated_call_caps(project):
    context = select(project, ())
    client = provider(context, limits=Limits(requests=2, retries=1))
    client.transport = Recording((429, b""), (200, response(context, "openai")))
    client.propose(context, max_response_bytes=MAX_RESPONSE)
    assert len(client.transport.calls) == 2
    with pytest.raises(ProviderError, match="budget"):
        client.propose(context, max_response_bytes=MAX_RESPONSE)
    for limits in [Limits(tokens=1), Limits(requests=1, retries=1)]:
        client = provider(context, limits=limits)
        client.transport = Recording((503, b""))
        with pytest.raises(ProviderError, match="budget"):
            client.propose(context, max_response_bytes=MAX_RESPONSE)
        assert len(client.transport.calls) <= 1


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"[]",
        b'{"choices":[]}',
        b'{"choices":[{"message":{"content":4}}]}',
        b"x" * (MAX_RESPONSE * 8 + 1),
    ],
    ids=["missing-fields", "array", "empty-choices", "nontext-content", "oversized"],
)
def test_invalid_wire_output(project, raw):
    context = select(project, ())
    client = provider(context)
    client.transport = Recording((200, raw))
    with pytest.raises(ProviderError, match="output"):
        client.propose(context, max_response_bytes=MAX_RESPONSE)


def test_output_budget(project):
    context = select(project, ())
    client = provider(context, limits=Limits(output_tokens=1))
    with pytest.raises(ProviderError, match="output"):
        client.propose(context, max_response_bytes=MAX_RESPONSE)


@pytest.mark.parametrize(
    "values",
    [
        {"requests": 0},
        {"requests": 5},
        {"tokens": 0},
        {"tokens": 262145},
        {"output_tokens": 0},
        {"output_tokens": 16385},
        {"retries": -1},
        {"retries": 4},
        {"timeout": 0},
        {"timeout": 121},
        {"timeout": float("nan")},
    ],
)
def test_invalid_limits(values):
    with pytest.raises(ProviderError):
        Limits(**values)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:123/api/chat",
        "http://example.com:123/api/chat",
        "http://127.0.0.1:123/api/chat?key=bad",
        "http://user:pass@127.0.0.1:123/api/chat",
        "http://127.0.0.1:no/api/chat",
        "http://[broken",
        "http://127.0.0.1:123/api/chat#bad",
        "https://127.0.0.1:123/api/chat",
        "http://127.0.0.1/api/chat",
    ],
)
def test_restrict_destination(endpoint):
    with pytest.raises(ProviderError):
        destination("ollama", endpoint)
    with pytest.raises(ProviderError):
        destination("openai", endpoint)


def test_ipv6_destination():
    assert destination("ollama", "http://[::1]:11434/api/chat") == "http://[::1]:11434/api/chat"


def test_credentials_lazy_and_isolated(project, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="manual"):
        credential()
    monkeypatch.setenv("OPENAI_API_KEY", "bad\nkey")
    with pytest.raises(ProviderError):
        credential()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    assert credential() == "synthetic-test-key"
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _: SimpleNamespace(get_password=lambda *args: "keyring-test-key"),
    )
    assert credential(keyring=True) == "keyring-test-key"

    def unavailable(_):
        raise ImportError("sensitive-key-and-context")

    monkeypatch.setattr(importlib, "import_module", unavailable)
    with pytest.raises(ProviderError, match="keyring unavailable"):
        credential(keyring=True)
    context = select(project, ())
    client = provider(context, "ollama", use_keyring=True)
    client.load_key = lambda **_: pytest.fail("local adapter must not load keys")
    client.propose(context, max_response_bytes=MAX_RESPONSE)


@pytest.mark.parametrize(
    "goal",
    ["", "x" * 4097, "api_key='synthetic-secret'", "-----BEGIN RSA PRIVATE KEY-----"],
    ids=["empty", "oversized", "credential", "private-key"],
)
def test_secret_and_goal_validation(project, goal):
    context = select(project, ())
    with pytest.raises(ProviderError):
        replace(provider(context), goal=goal).preview(context)


def test_model_validation_and_approval_binding(project):
    context = select(project, ())
    client = provider(context)
    with pytest.raises(ProviderError):
        replace(client, model="bad\nmodel").preview(context)
    original = client.preview(context)["approval_hash"]
    for change in [
        {"model": "other"},
        {"goal": "other"},
        {"limits": Limits(requests=2)},
        {"provider": "ollama"},
    ]:
        assert replace(client, **change).preview(context)["approval_hash"] != original


def arguments(project):
    return [
        "propose",
        str(project),
        "src/demo/_handlers.py",
        "--provider",
        "ollama",
        "--model",
        "synthetic-model",
        "--goal",
        "Improve description",
    ]


def test_cli_preview_propose_review_apply(project, capsys, monkeypatch):
    context = select(project, ("src/demo/_handlers.py",))
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert main(arguments(project)) == 0
    plan = json.loads(capsys.readouterr().out)
    assert main(["context", str(project), "src/demo/_handlers.py"]) == 0
    context_text = capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(arguments(project) + ["--approve-disclosure", "wrong"])
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(
        HTTPProvider,
        "propose",
        lambda *args, **kw: (
            Proposal(
                1,
                context.identity,
                SpecDelta("Improved description", ()),
                (
                    Patch(
                        "src/demo/_handlers.py",
                        context.files[0].base_hash,
                        "def run(command, values):\n    return {'answer': 42}\n",
                    ),
                ),
                (),
            )
            .canonical_json()
            .encode()
        ),
    )
    assert main(arguments(project) + ["--approve-disclosure", plan["approval_hash"]]) == 0
    proposal_text = capsys.readouterr().out
    assert all(p.read_bytes() == data for p, data in before.items())
    context_path = project / "saved-context.json"
    proposal_path = project / "saved-proposal.json"
    context_path.write_text(context_text)
    proposal_path.write_text(proposal_text)
    assert main(["review", str(project), str(context_path), str(proposal_path)]) == 0
    reviewed = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "apply",
                str(project),
                str(context_path),
                str(proposal_path),
                "--approve",
                reviewed["approval_hash"],
            ]
        )
        == 0
    )
    from scriptkit.conformance import validate

    assert validate(project).valid


@pytest.mark.parametrize(
    "error",
    [
        KeyboardInterrupt(),
        ProviderError("missing key; manual editing"),
        ValueError("sensitive-key-and-context"),
    ],
)
def test_cli_failure_no_writes(project, capsys, monkeypatch, error):
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    main(arguments(project))
    plan = json.loads(capsys.readouterr().out)

    def fail(*args, **kw):
        raise error

    monkeypatch.setattr(HTTPProvider, "propose", fail)
    args = arguments(project) + ["--approve-disclosure", plan["approval_hash"]]
    if isinstance(error, KeyboardInterrupt):
        assert main(args) == 130
    else:
        with pytest.raises(SystemExit):
            main(args)
    result = capsys.readouterr()
    assert not result.out and "sensitive" not in result.err
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize("status,body", [(200, b"{}"), (200, b"x" * 11), (302, b"secret")])
def test_transport_closes_and_refuses_redirect(monkeypatch, status, body):
    class Connection:
        closed = False

        def __init__(self, *args, **kw):
            pass

        def request(self, *args):
            pass

        def getresponse(self):
            return SimpleNamespace(status=status, read=lambda n: body[:n])

        def close(self):
            Connection.closed = True

    monkeypatch.setattr("scriptkit.ai.providers.http.client.HTTPSConnection", Connection)
    if len(body) > 10:
        with pytest.raises(ProviderError):
            http_post("https://api.openai.com/v1/chat/completions", b"{}", {}, 1, 10)
    else:
        assert http_post("https://api.openai.com/v1/chat/completions", b"{}", {}, 1, 10) == (
            status,
            body if status == 200 else b"",
        )
    assert Connection.closed


def test_key_echo_is_not_persisted(project):
    context = select(project, ())
    client = provider(context)
    client.transport = Recording(
        (200, b'{"choices":[{"message":{"content":"synthetic-test-key"}}]}')
    )
    with pytest.raises(ProviderError, match="output"):
        client.propose(context, max_response_bytes=MAX_RESPONSE)


def test_real_loopback_transport(project, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    context = select(project, ())
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    dict(self.headers),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            data = response(context, "ollama")
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        monkeypatch.setenv("OPENAI_API_KEY", "unrelated-cloud-key")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        client = HTTPProvider(
            "ollama",
            "synthetic-model",
            "Improve description",
            endpoint=f"http://127.0.0.1:{server.server_port}/api/chat",
        )
        result = client.propose(context, max_response_bytes=MAX_RESPONSE)
        assert Proposal.from_json(result.decode()).context_hash == context.identity
        assert "unrelated-cloud-key" not in json.dumps(received)
        assert "Authorization" not in received[0][0]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_missing_key_cli_no_request(project, capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    args = arguments(project)
    args[args.index("ollama")] = "openai"
    main(args)
    plan = json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit):
        main(args + ["--approve-disclosure", plan["approval_hash"]])
    result = capsys.readouterr()
    assert "manual editing" in result.err and not result.out


def test_transport_closes_on_interrupt(monkeypatch):
    class Connection:
        closed = False

        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            raise KeyboardInterrupt

        def close(self):
            Connection.closed = True

    monkeypatch.setattr("scriptkit.ai.providers.http.client.HTTPConnection", Connection)
    with pytest.raises(KeyboardInterrupt):
        http_post("http://127.0.0.1:11434/api/chat", b"{}", {}, 1, 10)
    assert Connection.closed


def test_installed_cli_help():
    result = subprocess.run(
        [sys.executable, "-I", "-m", "scriptkit.ai", "propose", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--approve-disclosure" in result.stdout

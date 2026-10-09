"""Execute the actual shell installers against a local HTTPS release fixture.

localhost.key is a PUBLIC TEST-ONLY key, not a deployment credential.
"""

from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import threading

import pytest

from test_bootstrap import built_wheel as built_wheel  # shared real build fixture


@pytest.fixture
def release_server(tmp_path, built_wheel):
    source = Path(__file__).resolve().parents[1]
    import scriptkit.bootstrap

    bootstrap = Path(scriptkit.bootstrap.__file__)
    shutil.copy(bootstrap, tmp_path / "bootstrap.py")
    shutil.copy(built_wheel, tmp_path / built_wheel.name)
    (tmp_path / "exit.py").write_bytes(b"raise SystemExit(17)\n")
    certs = source / "tests/fixtures/bootstrap"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certs / "localhost.pem", certs / "localhost.key")
    server = ThreadingHTTPServer(
        ("localhost", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield (
            f"https://localhost:{server.server_port}/",
            {
                **os.environ,
                "SSL_CERT_FILE": str(certs / "localhost.pem"),
                "SCRIPTKIT_PYTHON": sys.executable,
            },
            source,
            bootstrap,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def invocation(shell, source, url, digest, args):
    if shell != "sh":
        return [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(source / "install.ps1"),
            url,
            digest,
            *args,
        ]
    return [shell, str(source / "install.sh"), url, digest, *args]


@pytest.mark.parametrize("shell", ["pwsh", "powershell.exe"] if os.name == "nt" else ["sh"])
def test_shell_clean_install_and_exit_codes(tmp_path, release_server, built_wheel, shell):
    url, env, source, bootstrap = release_server
    root = tmp_path / "shell root café"
    digest = hashlib.sha256(bootstrap.read_bytes()).hexdigest()
    args = [
        "--root",
        str(root),
        "--wheel",
        url + built_wheel.name,
        "--sha256",
        hashlib.sha256(built_wheel.read_bytes()).hexdigest(),
        "--version",
        "1.5.0",
    ]
    command = invocation(shell, source, url + "bootstrap.py", digest, args)
    result = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["version"] == "1.5.0"
    assert not (root / "tools").exists()
    launcher = root / "bin" / ("scriptkit.cmd" if os.name == "nt" else "scriptkit")
    result = subprocess.run(
        [str(launcher), "--json", "doctor"], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["root"] == str(root.resolve())
    pointer = root / "manager-active.json"
    previous = json.loads(pointer.read_text())["target"]
    original_env = root / "manager-generations" / previous
    original_receipt = (original_env / "manager-receipt.json").read_bytes()
    for operation in (
        [
            "self-update",
            "--wheel",
            url + built_wheel.name,
            "--sha256",
            hashlib.sha256(built_wheel.read_bytes()).hexdigest(),
            "--version",
            "1.5.0",
        ],
        ["self-rollback"],
        ["doctor"],
    ):
        result = subprocess.run(
            [str(launcher), "--json", *operation],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["ok"]
        active = json.loads(pointer.read_text())
        if operation[0] == "self-update":
            assert active["target"] != previous
            assert active["previous"] == previous
        else:
            assert active["target"] == previous
    assert (original_env / "manager-receipt.json").read_bytes() == original_receipt
    result = subprocess.run(
        invocation(shell, source, url + "bootstrap.py", "0" * 64, args),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "SHA-256" in result.stderr
    exit_digest = hashlib.sha256(b"raise SystemExit(17)\n").hexdigest()
    result = subprocess.run(
        invocation(shell, source, url + "exit.py", exit_digest, []), env=env, capture_output=True
    )
    assert result.returncode == 17
    env["SCRIPTKIT_PYTHON"] = "scriptkit-python-not-installed"
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Python" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX usage branch")
def test_shell_usage():
    source = Path(__file__).resolve().parents[1]
    assert subprocess.run(["sh", str(source / "install.sh")], capture_output=True).returncode == 2

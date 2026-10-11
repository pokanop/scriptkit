import os
import socket
import subprocess
import sys
import time

import pytest

from scriptkit.cli import CliError
from scriptkit.execution import BoundedRunner
from scriptkit.proc import Result


def command(code):
    return [sys.executable, "-c", code]


def test_success_stdin_cwd_environment_and_result_contract(tmp_path):
    result = BoundedRunner().run(
        command(
            "import sys,os; print(sys.stdin.read()); print(os.getcwd()); "
            "print(os.environ['CUSTOM'],file=sys.stderr)"
        ),
        input="hello",
        cwd=tmp_path,
        env={**os.environ, "CUSTOM": "test"},
    )
    assert isinstance(result, Result)
    assert result.ok and bool(result)
    assert result.out.splitlines() == ["hello", str(tmp_path)]
    assert result.err == "test"


def test_missing_nonzero_and_sanitized_check():
    assert BoundedRunner().run(["scriptkit-no-such-executable-987654321"]).code == -127
    result = BoundedRunner().run(
        command("import sys; print('failure',file=sys.stderr); sys.exit(7)")
    )
    assert (result.code, result.err) == (7, "failure")
    with pytest.raises(CliError) as error:
        BoundedRunner().run(command("import sys; print('credential'); sys.exit(1)"), check=True)
    assert "credential" not in str(error.value)


def test_timeout_and_output_bound():
    result = BoundedRunner().run(command("import time; time.sleep(60)"), timeout=0.05)
    assert result.code == -1 and "timed out" in result.err
    result = BoundedRunner().run(command("import os; os.write(1,b'x'*1000000)"), max_output=10)
    assert result.code == -1 and "output limit" in result.err
    assert BoundedRunner().run(command("pass"), max_output=0).ok


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cmd": "echo bad"},
        {"cmd": []},
        {"cmd": [1]},
        {"cmd": ["x"], "timeout": 0},
        {"cmd": ["x"], "timeout": float("nan")},
        {"cmd": ["x"], "max_output": -1},
    ],
)
def test_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        BoundedRunner().run(**kwargs)


def test_cancellation_before_spawn():
    with pytest.raises(KeyboardInterrupt):
        BoundedRunner(cancelled=lambda: True).run(command("raise Exception('never')"))


def test_os_error_sanitized(monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("credential")

    monkeypatch.setattr(subprocess, "Popen", fail)
    result = BoundedRunner().run(["credential"])
    assert result.code == -1 and "credential" not in result.err


@pytest.mark.parametrize("reason", ["timeout", "cancel", "ctrl-c", "parent-exit"])
def test_owned_child_cleanup_and_unrelated_survives(tmp_path, reason):
    ready = tmp_path / "ready"
    child = (
        "import os,socket,time,pathlib; s=socket.socket(); s.bind(('127.0.0.1',0)); "
        f"p=pathlib.Path({str(ready)!r}); "
        "p.with_suffix('.tmp').write_text(str(s.getsockname()[1])); "
        "os.replace(p.with_suffix('.tmp'),p); time.sleep(60)"
    )
    parent = (
        f"import subprocess,sys,time,pathlib; subprocess.Popen([sys.executable,'-c',{child!r}]); "
    )
    if reason == "parent-exit":
        parent += f"\nwhile not pathlib.Path({str(ready)!r}).exists(): time.sleep(.01)"
    else:
        parent += "time.sleep(60)"
    unrelated = subprocess.Popen(command("import time; time.sleep(60)"))

    def sleep(seconds):
        if reason == "ctrl-c" and ready.exists():
            raise KeyboardInterrupt
        time.sleep(seconds)

    runner = BoundedRunner(cancelled=lambda: reason == "cancel" and ready.exists(), sleep=sleep)
    try:
        if reason in ("cancel", "ctrl-c"):
            with pytest.raises(KeyboardInterrupt):
                runner.run(command(parent), timeout=5)
        else:
            result = runner.run(command(parent), timeout=2)
            assert result.code == (0 if reason == "parent-exit" else -1)
        assert ready.exists(), "child did not start before timeout"
        port = int(ready.read_text())
        # Kernel teardown can follow the parent's wait; bound the test's readiness check.
        deadline = time.monotonic() + 3
        while True:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AssertionError("owned child retained its listening socket") from None
                    time.sleep(0.01)
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()

import argparse
import io
import json
import os
import subprocess
import sys

import pytest

from scriptkit.command import parse_args, run
from scriptkit.output import OutputContext, OutputPolicy


def make_context(machine=True):
    return OutputContext(OutputPolicy(machine=machine, rich=False), io.StringIO(), io.StringIO())


@pytest.mark.parametrize("machine", [False, True])
@pytest.mark.parametrize(
    "failure,code",
    [
        (ValueError("bad"), 1),
        (KeyboardInterrupt(), 130),
        (SystemExit(2), 2),
        (SystemExit("bad"), 1),
        (SystemExit(), 0),
    ],
)
def test_failure(machine, failure, code):
    out = make_context(machine)

    def main():
        raise failure

    assert run(out, main) == code
    if machine:
        assert json.loads(out.stdout.getvalue())["ok"] == (code == 0)
    if code:
        assert "error:" in out.stderr.getvalue()


def test_serialization_failure():
    out = make_context()
    assert run(out, lambda: object()) == 1
    assert json.loads(out.stdout.getvalue())["error"] == "result is not JSON serializable"


@pytest.mark.parametrize("machine", [False, True])
def test_result(machine):
    out = make_context(machine)
    assert run(out, lambda: "hello") == 0
    assert "hello" in out.stdout.getvalue()


def test_default_safety():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="version", version="1")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("delete")
    assert parse_args(parser, [], default="delete").command is None
    assert parse_args(parser, [], default="delete", allow_default=True).command == "delete"
    for args, code in [(["typo"], 2), (["--bad"], 2), (["--help"], 0), (["--version"], 0)]:
        with pytest.raises(SystemExit) as exc:
            parse_args(parser, args, default="delete", allow_default=True)
        assert exc.value.code == code


class Broken(io.StringIO):
    def write(self, text):
        raise BrokenPipeError

    def close(self):
        super().close()
        raise BrokenPipeError


def test_broken_pipe():
    out = make_context()
    out.stdout = Broken()
    assert run(out, lambda: "data") == 0
    assert out.stdout.closed


PROGRAM = """
import argparse, sys
from scriptkit.command import run, parse_args
from scriptkit.output import OutputContext, OutputPolicy
out = OutputContext(OutputPolicy(machine=True))
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', action='version', version='example 1')
    parser.add_argument('--fail', action='store_true')
    parser.add_argument('--interrupt', action='store_true')
    args = parse_args(parser, sys.argv[1:])
    if args.fail: raise ValueError('expected')
    if args.interrupt: raise KeyboardInterrupt
    print('incidental diagnostic')
    with out.progress('work') as task: task.advance()
    return {'answer': 42}
raise SystemExit(run(out, main))
"""


@pytest.mark.parametrize(
    "args,code",
    [
        ([], 0),
        (["--help"], 0),
        (["--version"], 0),
        (["--fail"], 1),
        (["--bad"], 2),
        (["--interrupt"], 130),
    ],
)
@pytest.mark.parametrize("force", ["", "1"])
def test_subprocess_machine_stdout(args, code, force):
    env = dict(os.environ, FORCE_COLOR=force, PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, "-c", PROGRAM, *args], capture_output=True, text=True, env=env, timeout=15
    )
    assert result.returncode == code, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["schema_version"] == 1
    assert envelope["ok"] == (code == 0)
    assert "\x1b" not in result.stdout
    if not args:
        assert envelope["data"] == {"answer": 42}
        assert "incidental diagnostic" in result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX pipe descriptor exercise; portable fake stream also tested"
)
def test_subprocess_closed_pipe():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(
            [sys.executable, "-c", PROGRAM], stdout=write_fd, stderr=subprocess.PIPE, timeout=15
        )
    finally:
        os.close(write_fd)
    assert result.returncode == 0, result.stderr
    assert b"BrokenPipe" not in result.stderr

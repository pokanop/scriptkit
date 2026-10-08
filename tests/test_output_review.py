import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scriptkit.command import run
from scriptkit.doctor import Check
from scriptkit.output import CommandResult, OutputContext, OutputPolicy, TableData


@pytest.mark.parametrize("rich", [True, False])
@pytest.mark.parametrize("ascii", [True, False])
def test_literal_results(rich, ascii):
    out = OutputContext(OutputPolicy(rich=rich, ascii=ascii, width=10), io.StringIO())
    text = "fe80::a:b:1 key:id:42 :x: " + "a" * 100
    out.result(text)
    assert out.stdout.getvalue() == text + "\n"


@pytest.mark.parametrize(
    "exc,message",
    [
        (RuntimeError(), "RuntimeError"),
        (AssertionError(), "AssertionError"),
        (SystemExit("goodbye"), "goodbye"),
        (SystemExit(7), "exited with status 7"),
    ],
)
@pytest.mark.parametrize("machine", [False, True])
def test_error_messages(exc, message, machine):
    out = OutputContext(OutputPolicy(machine=machine), io.StringIO(), io.StringIO())

    def fail():
        raise exc

    assert run(out, fail) == (7 if exc.args == (7,) else 1)
    assert message in out.stderr.getvalue()
    if machine:
        assert json.loads(out.stdout.getvalue())["error"] == message


def test_data_with_failure_and_duplicate_guard():
    out = OutputContext(OutputPolicy(machine=True), io.StringIO(), io.StringIO())
    sections = {"required": [Check.fail("missing")]}
    assert run(out, lambda: out.doctor_data(sections)) == 1
    result = json.loads(out.stdout.getvalue())
    assert result["data"]["required"][0]["state"] == "fail"
    assert not result["ok"]
    out.stdout = io.StringIO()
    assert run(out, lambda: out.doctor(sections)) == 1
    result = json.loads(out.stdout.getvalue())
    assert not result["ok"]
    assert "do not emit" in result["error"]
    out.stdout = io.StringIO()
    assert run(out, lambda: CommandResult({"partial": True}, 7, "partial result")) == 7
    assert json.loads(out.stdout.getvalue())["data"] == {"partial": True}


def test_unavailable_isatty():
    class Stream(io.StringIO):
        def isatty(self):
            raise OSError("closed")

    assert not OutputPolicy().use_color(Stream(), {})


def test_rich_table_golden():
    pytest.importorskip("rich")
    out = OutputContext(OutputPolicy(ascii=True), io.StringIO(), env={})
    out.table(TableData(("Name",), (("value",),)))
    assert out.stdout.getvalue() == "+-------+\n| Name  |\n|-------|\n| value |\n+-------+\n"


@pytest.mark.parametrize("args,code", [([], 0), (["--help"], 0), (["--bad"], 2)])
def test_example_machine(args, code):
    example = Path(__file__).resolve().parents[1] / "examples" / "output.py"
    result = subprocess.run(
        [sys.executable, str(example), "--json", *args], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == code
    assert json.loads(result.stdout)["ok"] == (code == 0)

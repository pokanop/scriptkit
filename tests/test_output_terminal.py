"""Real PTY smoke supplements portable deterministic fake-TTY golden tests."""

import io
import os
import subprocess
import sys

import pytest

from scriptkit.output import OutputContext, OutputPolicy, TableData


@pytest.mark.parametrize("rich", [False, True])
def test_forced_color_on_pipe_without_animation(rich):
    out = OutputContext(
        OutputPolicy(rich=rich), io.StringIO(), io.StringIO(), env={"FORCE_COLOR": "1"}
    )
    out.emit("error", "bad")
    assert "\x1b[" in out.stderr.getvalue()
    before = out.stderr.getvalue()
    with out.progress("never animate"):
        pass
    assert before == out.stderr.getvalue()


def test_ascii_rich_content():
    pytest.importorskip("rich")
    out = OutputContext(OutputPolicy(ascii=True), io.StringIO(), io.StringIO(), env={})
    out.table(TableData(("café",), (("✓",),)))
    assert out.stdout.getvalue().isascii()
    assert "\\u2713" in out.stdout.getvalue()


@pytest.mark.skipif(os.name == "nt", reason="PTY is POSIX-only; fake TTY tests run everywhere")
def test_subprocess_real_tty():
    import pty

    master, slave = pty.openpty()
    try:
        code = """
from scriptkit.output import OutputContext, OutputPolicy
out = OutputContext(OutputPolicy(color='never', ascii=True))
assert out.stderr.isatty()
with out.progress('work', total=1) as task:
    task.advance()
out.emit('success', 'finished')
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=slave,
            timeout=15,
        )
        assert result.returncode == 0
        # One short display fits in the PTY buffer; child completes before read.
        captured = os.read(master, 65536)
        assert b"success: finished" in captured
        assert captured.isascii()
        assert b"\x1b[32m" not in captured
        assert result.stdout == b""
    finally:
        os.close(master)
        os.close(slave)

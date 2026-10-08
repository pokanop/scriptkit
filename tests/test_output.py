"""Opt-in contracts; these tests also run against installed bare/Rich wheels."""

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from scriptkit.doctor import Check
from scriptkit.output import OutputContext, OutputPolicy, TableData, Theme


class Terminal(io.StringIO):
    def isatty(self):
        return True


def context(**options):
    return OutputContext(OutputPolicy(**options), io.StringIO(), io.StringIO(), env={})


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize(
    "env",
    [
        {},
        {"NO_COLOR": ""},
        {"NO_COLOR": "1"},
        {"FORCE_COLOR": ""},
        {"FORCE_COLOR": "1"},
        {"FORCE_COLOR": "0"},
        {"NO_COLOR": "", "FORCE_COLOR": "1"},
    ],
)
@pytest.mark.parametrize("color", ["auto", "always", "never"])
def test_color(tty, env, color):
    stream = Terminal() if tty else io.StringIO()
    expected = color == "always" or (
        color == "auto"
        and not env.get("NO_COLOR")
        and env.get("FORCE_COLOR") != "0"
        and (bool(env.get("FORCE_COLOR")) or tty)
    )
    assert OutputPolicy(color=color).use_color(stream, env) == expected
    assert not OutputPolicy(machine=True, color=color).use_color(stream, env)


def test_invalid_policy():
    with pytest.raises(ValueError, match="color"):
        OutputPolicy(color="invalid")
    with pytest.raises(ValueError, match="width"):
        OutputPolicy(width=9)


@pytest.mark.parametrize("rich", [False, True])
def test_diagnostics_golden(rich):
    out = context(rich=rich, ascii=True, quiet=True)
    out.emit("info", "hidden")
    out.emit("success", "hidden")
    out.emit("warning", "[literal] café")
    out.emit("error", "failed")
    assert out.stdout.getvalue() == ""
    assert out.stderr.getvalue() == "warning: [literal] caf\\xe9\nerror: failed\n"
    with pytest.raises(ValueError, match="level"):
        out.emit("unknown", "oops")


def test_machine_envelope():
    out = context(machine=True, ascii=True, color="always")
    out.result({"text": "café"})
    assert (
        out.stdout.getvalue()
        == '{"schema_version":1,"ok":true,"data":{"text":"caf\\u00e9"},"error":null}\n'
    )
    before = out.stdout.getvalue()
    with pytest.raises(ValueError):
        out.result(float("nan"))
    assert out.stdout.getvalue() == before


def test_table_plain_golden():
    out = context(rich=False, width=20)
    data = TableData(("Name", "Value"), (("alpha", "[x]"), ("long long long", "a\nb")))
    out.table(data)
    assert out.stdout.getvalue() == "Name     | Value\nalpha    | [x]\nlong lon | a b\n"
    with pytest.raises(ValueError, match="matching"):
        TableData(("a",), (("a", "b"),))
    with pytest.raises(ValueError):
        TableData((), ())
    machine = context(machine=True)
    machine.table(data, customize=lambda _: pytest.fail("machine must ignore presentation"))
    assert json.loads(machine.stdout.getvalue())["data"] == data.to_data()


def test_rich_table_shared_console():
    pytest.importorskip("rich")
    out = context(ascii=True, width=30)
    assert out.console() is out.console()
    assert out.console(diagnostic=True) is out.console(diagnostic=True)
    assert out.console() is not out.console(diagnostic=True)
    seen = []
    out.table(TableData(("a",), (("[bold]literal",),)), customize=lambda table: seen.append(table))
    assert len(seen) == 1
    assert "[bold]literal" in out.stdout.getvalue()
    assert out.stdout.getvalue().isascii()
    assert max(map(len, out.stdout.getvalue().splitlines())) <= 30
    out.theme = Theme(info="red")
    out.emit("info", "hello")


@pytest.mark.parametrize(
    "options",
    [{"machine": True}, {"quiet": True}, {"progress": False}, {"rich": False}, {"color": "always"}],
)
def test_no_animation_pipes(options):
    out = context(**options)
    with out.progress("work") as task:
        task.advance()
    assert out._progress is None
    assert out.stdout.getvalue() == out.stderr.getvalue() == ""


def test_monochrome_concurrent_progress_and_cancellation():
    pytest.importorskip("rich")
    out = context(color="never")
    out.stderr = Terminal()
    barrier = threading.Barrier(3)

    def work():
        with out.progress("work", total=2) as task:
            barrier.wait(timeout=10)
            task.advance()
            barrier.wait(timeout=10)
            return task

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work) for _ in range(2)]
        barrier.wait(timeout=10)
        assert out._tasks == 2
        assert out._progress is not None
        barrier.wait(timeout=10)
        for future in futures:
            future.result().advance()  # stale handle is harmless
    assert out._tasks == 0 and out._progress is None
    with pytest.raises(KeyboardInterrupt):
        with out.progress("cancel"):
            raise KeyboardInterrupt
    assert out._tasks == 0 and out._progress is None
    assert out.stderr.getvalue()  # live control sequences but no color escapes
    assert "\x1b[31m" not in out.stderr.getvalue()


def test_custom_progress_columns():
    rich = pytest.importorskip("rich.progress")
    out = context(ascii=True)
    out.stderr = Terminal()
    column = rich.TextColumn("custom {task.description}")
    with out.progress("work", columns=(column,)):
        assert out._progress.columns == (column,)


@pytest.mark.parametrize("options", [{"machine": True}, {"interactive": False}, {}])
def test_noninteractive(options):
    out = context(**options)
    assert out.ask("continue?", default="no") == "no"
    with pytest.raises(ValueError, match="noninteractive"):
        out.ask("required")


def test_interactive_and_eof():
    out = context(rich=False)
    out.stdin = Terminal("yes\n\n")
    assert out.ask("question") == "yes"
    assert out.ask("question", default="no") == "no"
    assert out.ask("question", default="EOF") == "EOF"
    with pytest.raises(ValueError, match="EOF"):
        out.ask("required")
    assert "question" in out.stderr.getvalue()


@pytest.mark.parametrize("machine", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_doctor(machine, failed):
    out = context(machine=machine, rich=False)
    checks = [Check.ok("Python", "available"), Check.warn("optional", hint="install")]
    if failed:
        checks.append(Check.fail("required", hint="fix"))
    assert out.doctor({"System": checks}) == int(failed)
    if machine:
        envelope = json.loads(out.stdout.getvalue())
        assert envelope["ok"] == (not failed)
        assert envelope["data"]["System"][0]["label"] == "Python"
    else:
        assert out.stdout.getvalue().startswith("System\nok: Python: available\n")
    assert out.stderr.getvalue() == ""

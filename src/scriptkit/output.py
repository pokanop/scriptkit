"""Opt-in output policy. Legacy module helpers are intentionally unchanged.

One context owns two shared consoles and one multiplexed progress display.
No global console replacement or runtime dependency on manager/provider layers.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, TextIO

from .doctor import Check

Color = Literal["auto", "always", "never"]


def _tty(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, OSError, ValueError):
        return False


@dataclass(frozen=True)
class OutputPolicy:
    machine: bool = False
    quiet: bool = False
    color: Color = "auto"
    progress: bool = True
    ascii: bool = False
    interactive: bool = True
    width: int = 80
    rich: bool = True

    def __post_init__(self) -> None:
        if self.color not in ("auto", "always", "never"):
            raise ValueError("color must be auto, always or never")
        if self.width < 10:
            raise ValueError("width must be at least 10")

    def use_color(self, stream: TextIO, env: Mapping[str, str]) -> bool:
        if self.machine or self.color == "never":
            return False
        if self.color == "always":
            return True
        if env.get("NO_COLOR"):
            return False
        if env.get("FORCE_COLOR"):
            return env["FORCE_COLOR"] != "0"
        return _tty(stream)


@dataclass(frozen=True)
class CommandResult:
    """Data plus process status for command.run; integers alone remain data."""

    data: object = None
    exit_code: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Theme:
    """Semantic Rich styles; text is always literal, not markup."""

    success: str = "bold green"
    error: str = "bold red"
    warning: str = "yellow"
    info: str = "dim cyan"
    heading: str = "bold cyan"


@dataclass(frozen=True)
class TableData:
    """Stable ordered data, independent of presentation and Rich availability."""

    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if not self.columns or any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("table requires columns and matching row lengths")

    def to_data(self) -> dict[str, object]:
        return {"columns": list(self.columns), "rows": [list(row) for row in self.rows]}


@dataclass
class ProgressTask:
    """Thread-safe task handle; late updates after cancellation are ignored."""

    _context: OutputContext
    _id: Any = None
    _closed: bool = False

    def advance(self, amount: float = 1) -> None:
        with self._context._lock:
            if not self._closed and self._context._progress is not None:
                self._context._progress.advance(self._id, amount)


@dataclass
class OutputContext:
    policy: OutputPolicy = field(default_factory=OutputPolicy)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    theme: Theme = field(default_factory=Theme)
    _consoles: dict[bool, Any] = field(default_factory=dict, init=False)
    _lock: Any = field(default_factory=threading.RLock, init=False)
    _progress: Any = field(default=None, init=False)
    _tasks: int = field(default=0, init=False)
    _in_command: bool = field(default=False, init=False)

    def console(self, *, diagnostic: bool = False) -> Any:
        """Get this context's shared Rich console, or None without Rich.

        Extensions must use this facility, never mutate the legacy globals.
        Machine-mode extensions must emit data rather than print renderables.
        """
        with self._lock:
            if diagnostic not in self._consoles:
                console = None
                if self.policy.rich:
                    try:
                        from rich.console import Console
                    except ImportError:
                        pass
                    else:
                        stream = self.stderr if diagnostic else self.stdout
                        color = self.policy.use_color(stream, self.env)
                        console = Console(
                            file=stream,
                            width=self.policy.width,
                            force_terminal=color or _tty(stream),
                            color_system="standard" if color else None,
                            no_color=not color,
                            markup=False,
                            emoji=False,
                            highlight=False,
                        )
                self._consoles[diagnostic] = console
            return self._consoles[diagnostic]

    def _literal(self, text: str) -> str:
        return (
            text.encode("ascii", "backslashreplace").decode("ascii") if self.policy.ascii else text
        )

    def _text(self, text: str, *, diagnostic: bool, style: str = "") -> None:
        text = self._literal(text)
        console = self.console(diagnostic=diagnostic)
        if console is not None:
            console.print(text, style=style, soft_wrap=True)
        else:
            stream = self.stderr if diagnostic else self.stdout
            codes = {
                "bold": "1",
                "dim": "2",
                "red": "31",
                "green": "32",
                "yellow": "33",
                "cyan": "36",
            }
            ansi = ";".join(codes[token] for token in style.split() if token in codes)
            if ansi and self.policy.use_color(stream, self.env):
                text = f"\x1b[{ansi}m{text}\x1b[0m"
            print(text, file=stream)

    def emit(self, level: str, message: str) -> None:
        """Implement contracts.Output; diagnostics always go to stderr."""
        if level not in ("success", "error", "warning", "info"):
            raise ValueError("unknown diagnostic level")
        if not self.policy.quiet or level in ("error", "warning"):
            self._text(f"{level}: {message}", diagnostic=True, style=getattr(self.theme, level))

    def result(self, data: object, *, ok: bool = True, error: str | None = None) -> None:
        """Write one complete v1 JSON envelope or human-readable result.

        Serialization is completed before writing; NaN and arbitrary objects fail.
        Quiet suppresses diagnostics, never explicitly requested result data.
        """
        if self.policy.machine:
            if self._in_command:
                raise ValueError(
                    "return data or CommandResult from command callbacks; do not emit results"
                )
            payload = json.dumps(
                {"schema_version": 1, "ok": ok, "data": data, "error": error},
                ensure_ascii=self.policy.ascii,
                allow_nan=False,
                separators=(",", ":"),
            )
            with self._lock:
                self.stdout.write(payload + "\n")
                self.stdout.flush()
        else:
            self._text(str(data), diagnostic=False)

    def table(self, data: TableData, *, customize: Callable[[Any], None] | None = None) -> None:
        if self.policy.machine:
            self.result(data.to_data())
            return
        console = self.console()
        if console is not None:
            from rich import box
            from rich.table import Table
            from rich.text import Text

            table = Table(
                box=box.ASCII if self.policy.ascii else box.ROUNDED,
                header_style=self.theme.heading,
            )
            for column in data.columns:
                table.add_column(Text(self._literal(column)))
            for row in data.rows:
                table.add_row(*(Text(self._literal(cell)) for cell in row))
            if customize is not None:
                customize(table)
            console.print(table)
        else:
            # Deterministic per-column bounds, even on very narrow terminals.
            width = max(1, (self.policy.width - 3 * (len(data.columns) - 1)) // len(data.columns))
            for row in (data.columns, *data.rows):
                cells = [
                    self._literal(cell).replace("\n", " ")[:width].ljust(width) for cell in row
                ]
                self._text(" | ".join(cells).rstrip(), diagnostic=False)

    @contextmanager
    def progress(
        self, description: str, *, total: float | None = None, columns: Sequence[Any] = ()
    ) -> Iterator[ProgressTask]:
        """Multiplex concurrent tasks; first active caller selects Rich columns.

        Animation depends on stderr TTY, not color. Never animate pipes/JSON.
        Tasks are removed on success, exception or generator cancellation.
        """
        task = ProgressTask(self)
        with self._lock:
            enabled = (
                self.policy.progress
                and not self.policy.machine
                and not self.policy.quiet
                and _tty(self.stderr)
                and self.console(diagnostic=True) is not None
            )
            if enabled:
                from rich.progress import BarColumn, Progress, TextColumn

                if self._progress is None:
                    self._progress = Progress(
                        *(
                            columns
                            or (
                                (TextColumn("{task.description} {task.completed}", markup=False),)
                                if self.policy.ascii
                                else (TextColumn("{task.description}", markup=False), BarColumn())
                            )
                        ),
                        console=self.console(diagnostic=True),
                        transient=True,
                    )
                    self._progress.start()
                task._id = self._progress.add_task(self._literal(description), total=total)
                self._tasks += 1
        try:
            yield task
        finally:
            with self._lock:
                task._closed = True
                if enabled:
                    self._progress.remove_task(task._id)
                    self._tasks -= 1
                    if not self._tasks:
                        self._progress.stop()
                        self._progress = None

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        """Never read redirected input or prompt in machine/noninteractive mode.

        A caller-supplied default is safe; otherwise fail rather than hang.
        EOF follows the same rule; interrupts propagate to the command boundary.
        """
        if not self.policy.interactive or self.policy.machine or not _tty(self.stdin):
            if default is not None:
                return default
            raise ValueError("input required in noninteractive mode")
        self._text(prompt, diagnostic=True)
        value = self.stdin.readline()
        if not value and default is None:
            raise ValueError("input required (EOF)")
        return value.strip() or default or ""

    def doctor_data(self, sections: Mapping[str, Sequence[Check]]) -> CommandResult:
        """Build a data-only report for command.run without emitting anything."""
        data = {
            title: [
                {"label": c.label, "state": c.state, "detail": c.detail, "hint": c.hint}
                for c in checks
            ]
            for title, checks in sections.items()
        }
        failed = any(c.state == "fail" for checks in sections.values() for c in checks)
        return CommandResult(data, int(failed), "checks failed" if failed else None)

    def doctor(self, sections: Mapping[str, Sequence[Check]]) -> int:
        """Standalone emitting report; use doctor_data inside command.run."""
        report = self.doctor_data(sections)
        if self.policy.machine:
            self.result(report.data, ok=report.exit_code == 0, error=report.error)
        else:
            for title, checks in sections.items():
                self._text(title, diagnostic=False, style=self.theme.heading)
                for check in checks:
                    self._text(
                        f"{check.state}: {check.label}: {check.detail} {check.hint}".rstrip(),
                        diagnostic=False,
                    )
        return report.exit_code

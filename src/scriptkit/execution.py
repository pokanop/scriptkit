"""Opt-in bounded subprocess runner retaining the legacy Result/code convention."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from ._windows_job import WindowsJob
from .cli import CliError
from .proc import Result


class ProcessRunner(Protocol):
    def run(self, cmd: Sequence[str], *, timeout: float = 300) -> Result: ...


class BoundedRunner:
    """Argument arrays only; cancellation re-raises after owned-tree cleanup.

    Output is spooled to private temporary files, never unbounded Python memory.
    Combined output is polled every 10ms: disk usage can overshoot the limit by
    bytes produced in that interval. Returned output is strictly byte-bounded.
    POSIX descendants that deliberately escape the session are outside this
    cooperative runner's boundary; use a sandbox for untrusted programs.
    """

    def __init__(
        self,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cancelled, self.sleep, self.clock = cancelled, sleep, clock

    def run(
        self,
        cmd: Sequence[str],
        *,
        timeout: float = 300,
        max_output: int = 1024 * 1024,
        input: str | None = None,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> Result:
        if isinstance(cmd, (str, bytes)) or not cmd or any(not isinstance(x, str) for x in cmd):
            raise ValueError("command must be a nonempty argument array")
        if not 0 < timeout < float("inf") or max_output < 0:
            raise ValueError("timeout must be finite and positive; output limit nonnegative")
        process: subprocess.Popen[bytes] | None = None
        job: WindowsJob | None = None
        result = Result(-1, "", "process failed")
        with (
            tempfile.TemporaryFile() as stdin,
            tempfile.TemporaryFile() as stdout,
            tempfile.TemporaryFile() as stderr,
        ):
            if input is not None:
                stdin.write(input.encode("utf-8"))
                stdin.seek(0)
            try:
                if self.cancelled():
                    raise KeyboardInterrupt
                if os.name == "nt":
                    job = WindowsJob()
                process = subprocess.Popen(
                    list(cmd),
                    shell=False,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=cwd,
                    env=env,
                    start_new_session=os.name != "nt",
                    creationflags=4 if os.name == "nt" else 0,  # CREATE_SUSPENDED
                )
                if job is not None:
                    job.start(int(getattr(process, "_handle")))
                deadline = self.clock() + timeout
                reason = ""
                while True:
                    if self.cancelled():
                        raise KeyboardInterrupt
                    if (
                        os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                        > max_output
                    ):
                        reason = "output limit exceeded"
                        break
                    if process.poll() is not None:
                        break
                    if self.clock() >= deadline:
                        reason = f"timed out after {timeout}s"
                        break
                    self.sleep(0.01)
                if reason:
                    result = Result(-1, "", reason)
                else:
                    stdout.seek(0)
                    stderr.seek(0)
                    out = stdout.read(max_output)
                    err = stderr.read(max_output - len(out))
                    result = Result(
                        process.wait(),
                        out.decode("utf-8", "replace").strip(),
                        err.decode("utf-8", "replace").strip(),
                    )
            except FileNotFoundError:
                result = Result(-127, "", "command not found")
            except OSError:
                # OS exceptions may contain arguments/credentials; never echo them.
                result = Result(-1, "", "process could not be started or observed")
            finally:
                if job is not None:
                    job.close()
                if process is not None:
                    if os.name != "nt":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if process.poll() is None:
                        process.kill()
                    process.wait()
        if check and not result.ok:
            # Unlike legacy check, do not echo potentially secret args or output.
            raise CliError(f"command failed (exit {result.code})")
        return result

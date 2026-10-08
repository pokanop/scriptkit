"""Native job integration runs on Windows; injectable WinAPI failures on every OS."""

import ctypes
from types import SimpleNamespace

import pytest

from scriptkit._windows_job import WindowsJob


class Function:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def api_fixture(monkeypatch, **results):
    functions = {
        "CreateJobObjectW": 123,
        "SetInformationJobObject": 1,
        "AssignProcessToJobObject": 1,
        "CloseHandle": 1,
        "NtResumeProcess": 0,
    }
    functions.update(results)
    api = SimpleNamespace(**{name: Function(result) for name, result in functions.items()})
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: api, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError("injected"), raising=False)
    return api


def test_job_assign_resume_close(monkeypatch):
    api = api_fixture(monkeypatch)
    job = WindowsJob()
    job.start(456)
    assert api.AssignProcessToJobObject.calls == [(123, 456)]
    assert api.NtResumeProcess.calls == [(456,)]
    job.close()
    job.close()
    assert api.CloseHandle.calls == [(123,)]


@pytest.mark.parametrize(
    "name,value",
    [
        ("CreateJobObjectW", 0),
        ("SetInformationJobObject", 0),
        ("AssignProcessToJobObject", 0),
        ("NtResumeProcess", -1),
    ],
)
def test_job_failure_cleanup(monkeypatch, name, value):
    api = api_fixture(monkeypatch, **{name: value})
    job = None
    try:
        with pytest.raises(OSError):
            job = WindowsJob()
            job.start(456)
    finally:
        if job:
            job.close()
    assert len(api.CloseHandle.calls) == (0 if name == "CreateJobObjectW" else 1)

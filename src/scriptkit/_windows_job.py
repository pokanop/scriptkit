"""Windows-only owned process-tree lifetime (no PID/name-based global cleanup)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


def _last_error() -> OSError:
    error: OSError = getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
    return error


class _Basic(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("minimum", ctypes.c_size_t),
        ("maximum", ctypes.c_size_t),
        ("active", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IO(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")
    ]


class _Limits(ctypes.Structure):
    _fields_ = [
        ("basic", _Basic),
        ("io", _IO),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


class WindowsJob:
    """Assign a suspended process before resuming, eliminating the spawn race."""

    def __init__(self) -> None:
        self.api = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise _last_error()
        limits = _Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = _last_error()
            self.close()
            raise error

    def start(self, process_handle: int) -> None:
        if not self.api.AssignProcessToJobObject(self.handle, process_handle):
            raise _last_error()
        api = getattr(ctypes, "WinDLL")("ntdll")
        api.NtResumeProcess.argtypes = [wintypes.HANDLE]
        api.NtResumeProcess.restype = wintypes.LONG
        if api.NtResumeProcess(process_handle) != 0:
            raise OSError("could not resume owned process")

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

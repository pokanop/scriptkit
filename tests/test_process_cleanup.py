import os
import signal
import subprocess
from unittest.mock import Mock

import pytest

from scriptkit.execution import _kill_group


@pytest.fixture(autouse=True)
def posix_signal(monkeypatch):
    # killpg is mocked in these tests; supply its POSIX signal on Windows too.
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)


@pytest.mark.parametrize("exited", [True, False])
def test_darwin_zombie_group_permission_error(monkeypatch, exited):
    process = Mock(pid=12345)
    process.wait.side_effect = None if exited else subprocess.TimeoutExpired("child", 0.1)
    kill = Mock(side_effect=[PermissionError(), ProcessLookupError()])
    monkeypatch.setattr(os, "killpg", kill, raising=False)
    _kill_group(process)
    process.wait.assert_called_once_with(timeout=0.1)
    assert kill.call_count == 2


@pytest.mark.parametrize("exited", [True, False])
def test_persistent_group_permission_failure_not_hidden(monkeypatch, exited):
    process = Mock(pid=12345)
    process.wait.side_effect = None if exited else subprocess.TimeoutExpired("child", 0.1)
    monkeypatch.setattr(os, "killpg", Mock(side_effect=PermissionError()), raising=False)
    with pytest.raises(PermissionError):
        _kill_group(process)

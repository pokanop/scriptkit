import os
import signal
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
    process.poll.return_value = 0 if exited else None
    kill = Mock(side_effect=[PermissionError(), ProcessLookupError()])
    monkeypatch.setattr(os, "killpg", kill, raising=False)
    if exited:
        _kill_group(process)
        assert kill.call_count == 2
    else:
        with pytest.raises(PermissionError):
            _kill_group(process)
        assert kill.call_count == 1


def test_persistent_group_permission_failure_not_hidden(monkeypatch):
    process = Mock(pid=12345)
    process.poll.return_value = 0
    monkeypatch.setattr(os, "killpg", Mock(side_effect=PermissionError()), raising=False)
    with pytest.raises(PermissionError):
        _kill_group(process)

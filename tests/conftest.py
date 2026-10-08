"""Runtime fixtures, extracted from pokanop/scripts (MIT).

No sys.path injection: tests must exercise an installed distribution.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_color(monkeypatch):
    import scriptkit.style as style

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    style.set_color(None)
    yield
    style.set_color(None)


@pytest.fixture
def no_color():
    import scriptkit.style as style

    style.set_color(False)
    return style

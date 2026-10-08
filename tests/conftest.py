"""Runtime fixtures, extracted from pokanop/scripts (MIT).

No sys.path injection: tests must exercise an installed distribution.
"""

import pytest


def pytest_collection_modifyitems(items):
    # pytest puts each node ID in PYTEST_CURRENT_TEST before fixture setup. Huge
    # auto-generated IDs can exceed Windows' environment-variable size limit.
    # Fail at collection on EVERY platform, without printing the oversized data.
    for item in items:
        if len(item.nodeid) > 1024:
            raise pytest.UsageError(
                "test node ID exceeds 1024 characters; give large parameters explicit short ids"
            )


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

"""Keep test metadata safe for Windows PYTEST_CURRENT_TEST before test execution."""

from types import SimpleNamespace

import pytest

from conftest import pytest_collection_modifyitems


def test_collection_accepts_short_ids():
    pytest_collection_modifyitems([SimpleNamespace(nodeid="test.py::test_value[oversized]")])


def test_collection_rejects_oversized_ids_without_echoing_data():
    with pytest.raises(pytest.UsageError, match="explicit short ids") as caught:
        pytest_collection_modifyitems([SimpleNamespace(nodeid="sensitive-fixture" * 100)])
    assert "sensitive-fixture" not in str(caught.value)

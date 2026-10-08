"""Pre-merge pin checks must reject tags and fail closed on API failures."""

import json
import subprocess

import pytest

from test_quality_tools import load


pins = load("check_action_pins")
SHA = "a" * 40


def test_find_deduplicate_and_accept_subpath(tmp_path):
    (tmp_path / "ci.yml").write_text(
        f"steps:\n  - uses: 'owner/action@{SHA}' # comment\n"
        f'  - uses: "owner/action/subdir@{SHA}"\n'
        "  - uses: ./.github/workflows/local.yml\n"
    )
    (tmp_path / "release.yaml").write_text(f"  uses: other/repo@{SHA}\n")
    assert pins.action_pins(tmp_path) == {("owner/action", SHA), ("other/repo", SHA)}


@pytest.mark.parametrize("reference", ["owner/action@v3", "owner/action@abc", "docker://image:tag"])
def test_reject_unpinned_reference(tmp_path, reference):
    (tmp_path / "ci.yml").write_text(f"  - uses: {reference}\n")
    with pytest.raises(ValueError, match="full commit pin"):
        pins.action_pins(tmp_path)


def test_reject_empty_discovery(tmp_path):
    with pytest.raises(ValueError, match="No remote action pins"):
        pins.action_pins(tmp_path)


def test_verified_commit(monkeypatch):
    def api(argv, **kwargs):
        assert argv == ["gh", "api", f"repos/owner/action/git/commits/{SHA}"]
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(argv, 0, json.dumps({"sha": SHA}))

    monkeypatch.setattr(pins.subprocess, "run", api)
    pins.verify_commit("owner/action", SHA)


@pytest.mark.parametrize("status", [422, 403, 500])
def test_tag_object_or_api_failure_is_not_ignored(monkeypatch, status):
    def api(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr=f"HTTP {status}")

    monkeypatch.setattr(pins.subprocess, "run", api)
    with pytest.raises(subprocess.CalledProcessError):
        pins.verify_commit("owner/action", SHA)


def test_reject_mismatched_response(monkeypatch):
    monkeypatch.setattr(
        pins.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, '{"sha": "other"}'),
    )
    with pytest.raises(ValueError, match="mismatch"):
        pins.verify_commit("owner/action", SHA)

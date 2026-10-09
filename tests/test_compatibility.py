"""Golden API generated from scripts commit 458104a, not the extracted copy."""

import inspect
import json
from pathlib import Path

import pytest
import scriptkit as sk


def test_public_exports_and_signatures():
    golden = json.loads(Path(__file__).with_name("public_api_1_3_0.json").read_text())
    assert sk.__all__ == golden["exports"]
    assert sk.__version__ == "1.4.0"
    for name in golden["exports"]:
        assert hasattr(sk, name)
    for name, signature in golden["signatures"].items():
        assert str(inspect.signature(getattr(sk, name))) == signature, name
    assert issubclass(sk.CliError, Exception)
    assert sk.run_cli is sk.cli.run
    assert (sk.EXIT_OK, sk.EXIT_ERROR, sk.EXIT_INTERRUPT) == (0, 1, 130)


def test_additive_artifact_policy_api():
    from scriptkit.contracts import ArtifactPolicy
    from scriptkit.contracts.ports import StreamingInstallationSource
    from scriptkit.manager import Installer
    from scriptkit.registry import Resolver
    from scriptkit.registry.source import RegistryArtifactSource

    import re

    golden = json.loads(Path(__file__).with_name("public_api_artifact_policy.json").read_text())
    actual = {
        cls.__name__: re.sub(r" at 0x[0-9a-fA-F]+", "", str(inspect.signature(cls))).replace(
            "pathlib._local.Path", "pathlib.Path"
        )
        for cls in (ArtifactPolicy, Installer, Resolver, RegistryArtifactSource)
    }
    actual["StreamingInstallationSource.fetch_into"] = str(
        inspect.signature(StreamingInstallationSource.fetch_into)
    )
    assert actual == golden


@pytest.mark.parametrize(
    "value, expected", [(None, 0), (True, 0), (False, 0), ("7", 0), (7, 7), (-1, -1)]
)
def test_exit_return_compatibility(value, expected):
    assert sk.run_cli(lambda: value) == expected


def test_interrupt_cleanup_failure_preserves_exit(capsys):
    def interrupt():
        raise KeyboardInterrupt

    def cleanup():
        raise RuntimeError("cleanup failed")

    assert sk.run_cli(interrupt, on_interrupt=cleanup) == 130
    assert "Interrupted" in capsys.readouterr().err


def test_system_exit_preserved():
    def exit_usage():
        raise SystemExit(2)

    with pytest.raises(SystemExit) as exc:
        sk.run_cli(exit_usage)
    assert exc.value.code == 2

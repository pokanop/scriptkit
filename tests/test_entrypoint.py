import pytest

from scriptkit.entrypoint import main


def test_bare_help(capsys):
    assert main([]) == 0
    assert "not yet available" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["-h", "--help", "--version"])
def test_metadata(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert "scriptkit" in capsys.readouterr().out.lower()


def test_unknown_command(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["install"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err

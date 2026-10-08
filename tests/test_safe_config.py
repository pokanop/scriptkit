import json

import pytest

from scriptkit.safe_config import ConfigError, SafeConfig
from scriptkit.state import StateConflict


@pytest.mark.parametrize(
    "raw", [b'{"x":', b"[]", b"null", b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}']
)
def test_malformed_never_overwritten(tmp_path, raw):
    path = tmp_path / "config"
    path.write_bytes(raw)
    config = SafeConfig(path)
    with pytest.raises(ConfigError):
        config.load()
    with pytest.raises(ConfigError, match="load"):
        config.save({})
    assert path.read_bytes() == raw


def test_precedence_coercion_and_cas(tmp_path):
    path = tmp_path / "config"
    path.write_text('{"web":{"port":80.0},"saved":true}')
    defaults = {"web": {"port": 1, "host": "local"}}
    config = SafeConfig(
        path,
        defaults=defaults,
        env_prefix="TOOL_",
        coerce_env=True,
        environ={"tool_WEB__PORT": "90", "OTHER": "x", "TOOL_": "ignored"},
    )
    assert config.load() == {"web": {"port": 90, "host": "local"}, "saved": True}
    assert defaults["web"]["port"] == 1
    config.save({"web": {"port": 100}})
    assert json.loads(path.read_bytes()) == {"web": {"port": 100}}
    config.save({"next": True})
    path.write_bytes(b"foreign")
    with pytest.raises(StateConflict):
        config.save({})
    assert path.read_bytes() == b"foreign"


def test_injectable_io_and_schema_no_value_leak(tmp_path):
    class MemoryIO:
        data = None

        def read(self, path):
            return self.data

        def replace(self, path, data, *, expected, mode):
            assert self.data == expected
            self.data = data

    def validator(data):
        if data.get("port") == "credential":
            raise ValueError("credential")

    io = MemoryIO()
    config = SafeConfig(tmp_path / "not-created", validator=validator, io=io)
    assert config.load() == {}
    config.save({"port": 80})
    with pytest.raises(ConfigError) as error:
        config.save({"port": "credential"})
    assert "credential" not in str(error.value)
    assert error.value.__suppress_context__
    assert not config.path.exists()


@pytest.mark.parametrize(
    "value",
    [
        "secret",
        {"source": "plain", "name": "key"},
        {"source": "env", "name": "KEY", "value": "secret"},
        {"source": "env", "name": "bad name"},
    ],
)
def test_secrets_are_references_only(tmp_path, value):
    config = SafeConfig(tmp_path / "config", secret_fields=("token",))
    config.load()
    with pytest.raises(ConfigError, match="references"):
        config.save({"token": value})


@pytest.mark.parametrize("source", ["env", "keyring"])
def test_secret_references_and_no_implicit_resolution(tmp_path, source):
    config = SafeConfig(tmp_path / "config", secret_fields=("token",), environ={"KEY": "secret"})
    config.load()
    config.save({"token": {"source": source, "name": "KEY"}})
    assert config.load()["token"] == {"source": source, "name": "KEY"}
    assert b"secret" not in config.path.read_bytes()


@pytest.mark.parametrize("data", [{"x": float("inf")}, {"x": object()}])
def test_non_json_save_preserves_file(tmp_path, data):
    config = SafeConfig(tmp_path / "config")
    config.load()
    config.save({"ok": 1})
    with pytest.raises(ConfigError):
        config.save(data)
    assert config.load() == {"ok": 1}


def test_non_object_save_and_uncoerced_environment(tmp_path):
    config = SafeConfig(tmp_path / "config", env_prefix="APP", environ={"APP_X": "true"})
    assert config.load() == {"x": "true"}
    with pytest.raises(ConfigError):
        config.save([])

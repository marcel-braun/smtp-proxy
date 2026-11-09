from dataclasses import replace

import pytest

from smtp_proxy.config import Settings, load_settings, save_settings
from smtp_proxy.paths import data_dir


def configured(**changes):
    return replace(Settings(upstream_host="smtp.example.com", upstream_username="user", password="secret"), **changes)


def test_roundtrip(tmp_path):
    settings = configured(password="p\u00e4ss'\\$!\n", upstream_username="user'\\${USER}")
    path = tmp_path / ".env"
    save_settings(settings, path)
    assert load_settings(path) == settings
    assert settings.password not in path.read_text()
    assert settings.password not in repr(settings)


def test_invalid_base64(tmp_path):
    path = tmp_path / ".env"
    path.write_text("UPSTREAM_PASSWORD_B64='not-base64!'\n")
    with pytest.raises(ValueError, match="Base64"):
        load_settings(path)


@pytest.mark.parametrize("changes", [
    {"local_port": 0}, {"upstream_port": 65536}, {"upstream_security": "auto"},
    {"upstream_host": "localhost", "upstream_port": 25}, {"force_from": "a@b\r\nX: bad"},
    {"smtp_timeout_seconds": 121}, {"max_queue_bytes": 1}, {"password": ""},
])
def test_invalid_settings(changes):
    with pytest.raises(ValueError):
        configured(**changes).validate()


def test_data_path_does_not_depend_on_cwd(tmp_path, monkeypatch):
    before = data_dir()
    monkeypatch.chdir(tmp_path)
    assert data_dir() == before
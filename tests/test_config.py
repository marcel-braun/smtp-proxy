from dataclasses import replace
import socket

import pytest

from smtp_proxy.config import Settings, allowed_networks, load_settings, save_settings
from smtp_proxy.paths import data_dir


def configured(**changes):
    return replace(Settings(upstream_host="smtp.example.com", upstream_username="user", password="secret"), **changes)


def test_roundtrip(tmp_path):
    settings = configured(password="p\u00e4ss'\\$!\n", upstream_username="user'\\${USER}",
                          upstream_accept_invalid_certificate=True,
                          allowed_hosts="127.0.0.1, 10.1.2.0/24, client.example.com",
                          local_security="starttls", local_certificate="server.pem",
                          local_private_key="server-key.pem")
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
    {"allowed_hosts": ""}, {"allowed_hosts": "*"}, {"allowed_hosts": "10.0.0.1,"},
    {"allowed_hosts": "999.999.999.999"}, {"local_security": "auto"},
    {"local_security": "tls", "local_certificate": "cert.pem"},
])
def test_invalid_settings(changes):
    with pytest.raises(ValueError):
        configured(**changes).validate()


@pytest.mark.parametrize("mode", ["starttls", "tls"])
def test_automatic_certificate_configuration(mode):
    configured(local_security=mode).validate()


def test_data_path_does_not_depend_on_cwd(tmp_path, monkeypatch):
    before = data_dir()
    monkeypatch.chdir(tmp_path)
    assert data_dir() == before


def test_allowed_hostname_resolves_to_peer_addresses(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 0, 0, 0)),
    ])
    networks = allowed_networks("client.example.com")
    assert {str(network) for network in networks} == {"192.0.2.10/32", "2001:db8::1/128"}


def test_unresolved_allowed_hostname_fails_closed(monkeypatch):
    def unavailable(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "not found")

    monkeypatch.setattr(socket, "getaddrinfo", unavailable)
    with pytest.raises(ValueError, match="aufgeloest"):
        allowed_networks("client.example.com")
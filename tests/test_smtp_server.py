import smtplib
import socket
import ssl
from unittest.mock import Mock

import pytest
import trustme

from smtp_proxy.queue_store import QueueStore
from smtp_proxy.smtp_server import LocalServer
from test_config import configured


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_smtp_persists_before_accepting(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    settings = configured(local_port=free_port())
    server = LocalServer(store, settings)
    server.start()
    content = b"From: a@example.com\r\nTo: hidden@example.com\r\n\r\n.Line\r\n"
    try:
        with smtplib.SMTP("127.0.0.1", settings.local_port) as client:
            client.ehlo()
            assert not client.has_extn("auth")
            assert not client.has_extn("smtputf8")
            assert client.sendmail("a@example.com", ["real@example.com"], content) == {}
            job = store.next_due(120)
            assert job["content"] == content
            assert job["recipients"][0]["address"] == "real@example.com"
        assert server.controllers[0].hostname == "0.0.0.0"
    finally:
        server.stop()


def test_disk_failure_is_not_accepted(tmp_path):
    store = Mock()
    store.enqueue.side_effect = OSError("disk full")
    settings = configured(local_port=free_port())
    server = LocalServer(store, settings)
    server.start()
    try:
        with smtplib.SMTP("127.0.0.1", settings.local_port) as client:
            with pytest.raises(smtplib.SMTPDataError) as raised:
                client.sendmail("a@b", ["c@d"], b"Subject: test\r\n\r\nBody")
            assert raised.value.smtp_code == 451
    finally:
        server.stop()


def test_port_conflict_is_reported(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = LocalServer(QueueStore(tmp_path / "queue.sqlite3"), configured(local_port=port))
        with pytest.raises(OSError):
            server.start()
        assert not server.controllers


@pytest.mark.parametrize("allowed", ["127.0.0.1", "127.0.0.0/8", "localhost"])
def test_allowlisted_client_can_send(tmp_path, allowed):
    store = QueueStore(tmp_path / "queue.sqlite3")
    settings = configured(local_port=free_port(), allowed_hosts=allowed)
    server = LocalServer(store, settings)
    server.start()
    try:
        with smtplib.SMTP("127.0.0.1", settings.local_port) as client:
            assert client.sendmail("a@b", ["c@d"], b"Subject: test\r\n\r\nBody") == {}
        assert store.next_due(120) is not None
    finally:
        server.stop()


@pytest.mark.parametrize("mode", ["none", "starttls", "tls"])
def test_denied_client_cannot_spoof_allowed_hostname(tmp_path, mode):
    store = QueueStore(tmp_path / "queue.sqlite3")
    arguments, context = tls_settings(tmp_path) if mode != "none" else ({}, None)
    settings = configured(local_port=free_port(), allowed_hosts="192.0.2.1",
                          local_security=mode, **arguments)
    server = LocalServer(store, settings)
    server.start()
    try:
        client = (smtplib.SMTP_SSL("127.0.0.1", settings.local_port, context=context)
                  if mode == "tls" else smtplib.SMTP("127.0.0.1", settings.local_port))
        with client:
            if mode == "starttls":
                client.starttls(context=context)
            client.ehlo("192.0.2.1")
            with pytest.raises(smtplib.SMTPSenderRefused) as raised:
                client.sendmail("a@b", ["c@d"], b"Subject: test\r\n\r\nBody")
            assert raised.value.smtp_code == 550
        assert store.next_due(120) is None
    finally:
        server.stop()


def tls_settings(tmp_path):
    authority = trustme.CA()
    certificate = authority.issue_cert("127.0.0.1")
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server-key.pem"
    cert_path.write_bytes(b"".join(pem.bytes() for pem in certificate.cert_chain_pems))
    certificate.private_key_pem.write_to_path(key_path)
    context = ssl.create_default_context()
    authority.configure_trust(context)
    return {"local_certificate": str(cert_path), "local_private_key": str(key_path)}, context


@pytest.mark.parametrize("mode", ["starttls", "tls"])
def test_tls_delivery(tmp_path, mode):
    arguments, context = tls_settings(tmp_path)
    settings = configured(local_port=free_port(), local_security=mode, **arguments)
    store = QueueStore(tmp_path / "queue.sqlite3")
    server = LocalServer(store, settings)
    server.start()
    try:
        if mode == "tls":
            client = smtplib.SMTP_SSL("127.0.0.1", settings.local_port, context=context)
        else:
            client = smtplib.SMTP("127.0.0.1", settings.local_port)
            client.ehlo()
            assert client.mail("a@b")[0] == 530
            assert store.next_due(120) is None
            client.starttls(context=context)
        with client:
            assert client.sendmail("a@b", ["c@d"], b"Subject: test\r\n\r\nBody") == {}
        assert store.next_due(120) is not None
    finally:
        server.stop()


def test_invalid_certificate_prevents_listener_start(tmp_path):
    settings = configured(local_port=free_port(), local_security="tls",
                          local_certificate=str(tmp_path / "missing.pem"),
                          local_private_key=str(tmp_path / "missing-key.pem"))
    server = LocalServer(QueueStore(tmp_path / "queue.sqlite3"), settings)
    with pytest.raises(ValueError, match="unvollstaendig"):
        server.start()
    assert not server.controllers


@pytest.mark.parametrize("mode", ["starttls", "tls"])
def test_automatic_certificate_is_trusted_and_reused(tmp_path, mode):
    settings = configured(local_port=free_port(), local_security=mode)
    store = QueueStore(tmp_path / "queue.sqlite3")
    server = LocalServer(store, settings)
    server.start()
    certificate_path = tmp_path / "tls" / "server.pem"
    key_path = tmp_path / "tls" / "server-key.pem"
    original = certificate_path.read_bytes(), key_path.read_bytes()
    context = ssl.create_default_context(cafile=str(certificate_path))
    try:
        client = (smtplib.SMTP_SSL("127.0.0.1", settings.local_port, context=context)
                  if mode == "tls" else smtplib.SMTP("127.0.0.1", settings.local_port))
        with client:
            if mode == "starttls":
                client.starttls(context=context)
            assert client.sendmail("a@b", ["c@d"], b"Subject: test\r\n\r\nBody") == {}
    finally:
        server.stop()
    server.start()
    try:
        assert original == (certificate_path.read_bytes(), key_path.read_bytes())
        import os

        if os.name != "nt":
            assert key_path.stat().st_mode & 0o777 == 0o600
    finally:
        server.stop()


def test_starttls_upgrade_does_not_consume_another_connection_slot(tmp_path):
    arguments, context = tls_settings(tmp_path)
    settings = configured(local_port=free_port(), local_security="starttls", **arguments)
    server = LocalServer(QueueStore(tmp_path / "queue.sqlite3"), settings)
    server.start()
    try:
        with smtplib.SMTP("127.0.0.1", settings.local_port) as client:
            client.ehlo()
            active = server.controllers[0].smtpd.active
            client.starttls(context=context)
            client.ehlo()
            assert server.controllers[0].smtpd.active == active
    finally:
        server.stop()
import smtplib
import socket
from unittest.mock import Mock

import pytest

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
        assert all(controller.hostname in {"127.0.0.1", "::1"} for controller in server.controllers)
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
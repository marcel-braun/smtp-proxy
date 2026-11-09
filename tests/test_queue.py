from unittest.mock import Mock

import pytest

from smtp_proxy.queue_store import QueueFullError, QueueStore
from smtp_proxy.relay import prepare_message
from smtp_proxy.worker import QueueWorker
from test_config import configured


def test_durable_queue_and_quota(tmp_path):
    path = tmp_path / "queue.sqlite3"
    store = QueueStore(path)
    identifier = store.enqueue("a@b", ["c@d"], b"raw\r\n", max_bytes=8)
    assert QueueStore(path).next_due(120)["content"] == b"raw\r\n"
    with pytest.raises(QueueFullError):
        store.enqueue("a@b", ["c@d"], b"more", max_bytes=8)
    store.record(identifier, {"c@d": ("sent", 0, "")})
    assert store.list_messages() == []


def test_partial_delivery_not_repeated(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    identifier = store.enqueue("a@b", ["ok@d", "later@d", "bad@d"], b"mail", max_bytes=100)
    worker = QueueWorker(store, configured())
    worker.client = Mock()
    worker.client.send.return_value = {"later@d": (450, b"busy"), "bad@d": (550, b"no")}
    assert worker.process_one()
    rows = {item["address"]: item["status"] for item in store.list_messages()}
    assert rows == {"ok@d": "sent", "later@d": "pending", "bad@d": "failed"}
    store.retry(identifier)
    worker.client.send.return_value = {}
    worker.process_one()
    assert set(worker.client.send.call_args.args[1]) == {"later@d", "bad@d"}
    assert store.list_messages() == []


def test_auth_error_retains_message(tmp_path):
    import smtplib

    store = QueueStore(tmp_path / "queue.sqlite3")
    store.enqueue("a@b", ["c@d"], b"mail", max_bytes=100)
    worker = QueueWorker(store, configured())
    worker.client = Mock()
    worker.client.send.side_effect = smtplib.SMTPAuthenticationError(535, b"secret must not leak")
    worker.process_one()
    row = store.list_messages()[0]
    assert row["status"] == "pending"
    assert "secret" not in row["error"]


def test_rewrite_and_empty_reverse_path():
    content = b"From: old@example.com\r\nTo: a@b\r\n\r\nBody\r\n"
    assert prepare_message("", content, "new@example.com") == ("", content)
    assert prepare_message("old@example.com", content, "") == ("old@example.com", content)
    sender, rewritten = prepare_message("old@example.com", content, "new@example.com")
    assert sender == "new@example.com"
    assert b"From: new@example.com" in rewritten
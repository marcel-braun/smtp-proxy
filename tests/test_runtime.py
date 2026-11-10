import smtplib

import pytest

from smtp_proxy.queue_store import QueueStore
from smtp_proxy.runtime import ProxyRuntime
from test_config import configured
from test_smtp_server import free_port


def test_runtime_lock_and_restart(tmp_path):
    settings = configured(local_port=free_port(), upstream_host="127.0.0.1",
                          upstream_port=free_port(), upstream_security="none",
                          smtp_timeout_seconds=1)
    runtime = ProxyRuntime(settings, tmp_path)
    runtime.start()
    try:
        duplicate = ProxyRuntime(settings, tmp_path)
        with pytest.raises(RuntimeError, match="Instanz"):
            duplicate.start()
        with smtplib.SMTP("127.0.0.1", settings.local_port, timeout=5) as client:
            client.sendmail("sender@example.com", ["recipient@example.com"],
                            b"Subject: persisted\r\n\r\nbody\r\n")
        assert QueueStore(tmp_path / "queue.sqlite3").list_messages()
    finally:
        runtime.stop()
    restarted = ProxyRuntime(settings, tmp_path)
    restarted.start()
    try:
        assert QueueStore(tmp_path / "queue.sqlite3").list_messages()
    finally:
        restarted.stop()
    assert runtime.lock_file is None
    assert restarted.lock_file is None
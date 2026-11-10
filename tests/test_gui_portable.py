from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from smtp_proxy.config import load_settings, save_settings
from smtp_proxy.gui import main_window as gui
from smtp_proxy.portable import RuntimeActiveError, idle_runtime, runtime_status
from smtp_proxy.queue_store import QueueStore
from test_config import configured
from test_smtp_server import free_port, tls_settings


@pytest.fixture
def portable_window(qtbot, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Portable GUI must not access SCM or protected storage")

    if gui.service_manager is not None:
        for name in ("is_admin", "status", "autostart_enabled", "initialize_data_dir",
                     "start", "stop", "restart", "install", "set_autostart", "remove",
                     "elevate_gui"):
            monkeypatch.setattr(gui.service_manager, name, forbidden)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    window = gui.MainWindow(directory=tmp_path)
    window.timer.stop()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy, timeout=10000)
    yield window
    qtbot.waitUntil(lambda: not window.busy, timeout=10000)
    window.dirty = False
    window.close()


def test_portable_gui_can_create_shared_configuration(portable_window, qtbot, tmp_path):
    window = portable_window
    assert window.ready
    assert window.portable
    assert window.mode_label.text() == "Portabel"
    assert not window.buttons["apply"].isVisible()
    assert "install" not in window.buttons
    assert not window.inputs["service_autostart"].isEnabled()
    settings = configured(allowed_hosts="127.0.0.1, 192.0.2.10", local_security="starttls",
                          local_certificate="server.pem", local_private_key="server-key.pem")
    window._populate(settings)
    window.save()
    qtbot.waitUntil(lambda: not window.busy, timeout=10000)
    assert load_settings(tmp_path / ".env") == settings
    assert window.state == "stopped"
    assert not window.runtime_pending


def test_portable_gui_loads_same_directory(portable_window, qtbot, tmp_path):
    settings = configured(local_port=2525)
    save_settings(settings, tmp_path / ".env")
    portable_window.reload_settings()
    qtbot.waitUntil(lambda: not portable_window.busy, timeout=10000)
    assert portable_window._settings() == settings


def test_portable_saving_while_running_requires_restart(tmp_path):
    with idle_runtime(tmp_path):
        snapshot, pending = gui.persist_portable_settings(configured(), tmp_path)
    assert snapshot == ("running", None)
    assert pending


def test_queue_mutation_excludes_active_runtime(tmp_path):
    store = QueueStore(tmp_path / "queue.sqlite3")
    identifier = store.enqueue("a@b", ["c@d"], b"mail", max_bytes=100)
    with idle_runtime(tmp_path):
        assert runtime_status(tmp_path) == "running"
        with pytest.raises(RuntimeActiveError):
            gui.mutate_portable_queue(tmp_path, "delete", identifier)
    assert runtime_status(tmp_path) == "stopped"
    assert gui.mutate_portable_queue(tmp_path, "delete", identifier) == []


def test_portable_testmail_uses_no_scm(monkeypatch):
    if gui.service_manager is not None:
        monkeypatch.setattr(gui.service_manager, "status", lambda: pytest.fail("SCM accessed"))
    smtp = Mock()
    smtp.__enter__ = Mock(return_value=smtp)
    smtp.__exit__ = Mock(return_value=False)
    smtp.send_message.return_value = {}
    monkeypatch.setattr(gui.smtplib, "SMTP", Mock(return_value=smtp))
    gui.send_testmail(configured(), "to@example.com", "from@example.com", portable=True)
    smtp.send_message.assert_called_once()


@pytest.mark.parametrize("mode", ["starttls", "tls"])
@pytest.mark.parametrize("automatic", [False, True])
def test_portable_testmail_uses_inbound_tls(tmp_path, mode, automatic):
    from smtp_proxy.smtp_server import LocalServer

    arguments = {} if automatic else tls_settings(tmp_path)[0]
    settings = configured(local_port=free_port(), local_security=mode, **arguments)
    store = QueueStore(tmp_path / "queue.sqlite3")
    server = LocalServer(store, settings)
    server.start()
    try:
        gui.send_testmail(settings, "to@example.com", "from@example.com",
                  portable=True, directory=tmp_path)
        assert store.next_due(120) is not None
    finally:
        server.stop()
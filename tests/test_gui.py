import os
import subprocess
import sys
import threading
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

if sys.platform != "win32":
    pytest.skip("Windows service GUI tests require Windows", allow_module_level=True)

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox

from smtp_proxy import config, service_manager
from smtp_proxy.gui import main_window as gui
from smtp_proxy.gui.main_window import MainWindow

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REAL_LOAD = config.load_settings
REAL_SAVE = config.save_settings
REAL_QUEUE = gui.QueueStore


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected production data access")

    monkeypatch.setattr(service_manager, "is_admin", lambda: False)
    monkeypatch.setattr(service_manager, "status", lambda: "stopped")
    monkeypatch.setattr(service_manager, "autostart_enabled", forbidden)
    monkeypatch.setattr(service_manager, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "initialize_data_dir", forbidden)
    monkeypatch.setattr(config, "load_settings", forbidden)
    monkeypatch.setattr(config, "save_settings", forbidden)
    monkeypatch.setattr(gui, "QueueStore", forbidden)
    for name in ("install", "start", "stop", "restart", "remove", "set_autostart", "elevate_gui"):
        monkeypatch.setattr(service_manager, name, forbidden)
    monkeypatch.setattr(gui, "license_directory", lambda: tmp_path / "_internal" / "licenses")
    return tmp_path


@pytest.fixture
def dialogs(monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    return messages


@pytest.fixture
def windows(qtbot, isolated, dialogs):
    opened = []

    def create(**kwargs):
        window = MainWindow(**kwargs)
        window.timer.stop()
        opened.append(window)
        qtbot.addWidget(window)
        window.show()
        qtbot.waitUntil(lambda: not window.busy, timeout=10000)
        return window

    yield create
    for window in opened:
        qtbot.waitUntil(lambda: not window.busy, timeout=10000)
        window.timer.stop()
        window.dirty = False
        window.close()


@pytest.fixture
def admin_env(monkeypatch, isolated):
    events = []
    state = {"status": "stopped", "startup": False}
    settings = config.Settings(upstream_host="smtp.example.com", upstream_username="user",
                               password="sensitive-password", force_from="proxy@example.com")
    REAL_SAVE(settings, isolated / ".env")

    def initialize():
        events.append("initialize")
        return isolated

    def load(path):
        events.append("load")
        assert path == isolated / ".env"
        return REAL_LOAD(path)

    def save(settings, path, **kwargs):
        events.append("save")
        assert path == isolated / ".env"
        REAL_SAVE(settings, path, **kwargs)

    def startup(enabled):
        events.append(("startup", enabled))
        state["startup"] = enabled

    def operation(name):
        events.append(name)
        if name in {"start", "restart"}:
            state["status"] = "running"
        elif name == "stop":
            state["status"] = "stopped"
        elif name == "remove":
            state["status"] = "not_installed"
        elif name == "install":
            state["status"] = "stopped"

    monkeypatch.setattr(service_manager, "is_admin", lambda: True)
    monkeypatch.setattr(service_manager, "initialize_data_dir", initialize)
    monkeypatch.setattr(gui, "application_dir", lambda: isolated)
    monkeypatch.setattr(gui, "service_settings_path", lambda root: isolated / ".env")
    monkeypatch.setattr(service_manager, "protect_settings_file", lambda path: None)
    monkeypatch.setattr(service_manager, "verify_settings_file", lambda path: None)
    monkeypatch.setattr(service_manager, "status", lambda: state["status"])
    monkeypatch.setattr(service_manager, "installed", lambda: state["status"] != "not_installed")
    monkeypatch.setattr(service_manager, "autostart_enabled", lambda: state["startup"])
    monkeypatch.setattr(service_manager, "set_autostart", startup)
    for name in ("install", "start", "stop", "restart", "remove"):
        monkeypatch.setattr(service_manager, name, lambda action=name: operation(action))
    monkeypatch.setattr(config, "load_settings", load)
    monkeypatch.setattr(config, "save_settings", save)
    monkeypatch.setattr(gui, "QueueStore", REAL_QUEUE)
    return isolated, settings, events, state


def wait_idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.busy, timeout=10000)


@pytest.mark.parametrize("read_only", [True, False])
def test_read_only_never_loads_secrets_or_initializes_storage(windows, isolated, read_only):
    window = windows(read_only=read_only)
    assert window.windowTitle() == "SMTPProxy"
    assert not window.admin
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "Status", "Einstellungen", "Warteschlange", "Protokoll", "About",
    ]
    assert not list(isolated.iterdir())
    assert window.state == "stopped"
    assert not window.inputs


def test_read_only_overrides_admin_and_smoke_has_no_scm(windows, monkeypatch):
    def forbidden():
        pytest.fail("Smoke test queried SCM or administrator state")

    monkeypatch.setattr(service_manager, "is_admin", forbidden)
    monkeypatch.setattr(service_manager, "status", forbidden)
    window = windows(read_only=True, auto_refresh=False)
    assert not window.admin
    assert not window.busy


def test_about_and_explicit_elevation(windows, monkeypatch, qtbot):
    calls = []
    monkeypatch.setattr(service_manager, "elevate_gui", lambda: calls.append("elevate"))
    window = windows()
    labels = [label.text() for label in window.findChildren(QLabel)]
    for text in ("Marcel Braun", "marcel.braun@ferchau.com", "Version 1.0.0"):
        assert text in labels
    assert not calls
    window.elevate()
    wait_idle(qtbot, window)
    assert calls == ["elevate"]


def test_admin_initializes_before_load_and_all_settings_available(windows, admin_env):
    root, settings, events, state = admin_env
    window = windows()
    assert events[:2] == ["initialize", "load"]
    assert window.ready
    assert set(window.inputs) == {field.name for field in fields(config.Settings)}
    assert window._settings() == settings
    assert window.inputs["password"].echoMode() == QLineEdit.Password
    window.show_password.setChecked(True)
    assert window.inputs["password"].echoMode() == QLineEdit.Normal
    assert "Base64" in window.inputs["password"].toolTip()
    assert "unverschl\u00fcsselt" in window.inputs["upstream_security"].toolTip()
    window.inputs["max_queue_bytes"].setText("10000000000")
    assert window._settings().max_queue_bytes == 10000000000


def test_certificate_override_is_explicit_and_warned(windows, admin_env):
    window = windows()
    option = window.inputs["upstream_accept_invalid_certificate"]
    assert option.text() == "Ungueltige TLS-Zertifikate immer akzeptieren (unsicher)"
    assert "abgefangen" in option.toolTip()
    assert not option.isChecked()


def test_status_poll_preserves_settings_focus_and_save_button(windows, admin_env, qtbot):
    window = windows()
    window.tabs.setCurrentIndex(1)
    qtbot.wait(50)
    field = window.inputs["upstream_host"]
    field.setFocus()
    release = threading.Event()
    assert window._submit(lambda: release.wait(5), status_query=True)
    try:
        assert field.isEnabled()
        assert window.focusWidget() is field
        assert window.buttons["save"].isVisible()
        assert window.buttons["save"].text() == "Speichern"
    finally:
        release.set()
        wait_idle(qtbot, window)


@pytest.mark.parametrize("state_value", ["stopped", "not_installed", "running"])
def test_save_order_without_implicit_restart(windows, admin_env, qtbot, state_value):
    root, settings, events, state = admin_env
    state["status"] = state_value
    window = windows()
    events.clear()
    window.inputs["service_autostart"].setChecked(True)
    window.save()
    wait_idle(qtbot, window)
    assert events[:2] == ["initialize", "save"]
    assert REAL_LOAD(root / ".env").service_autostart
    if state_value != "not_installed":
        assert events[2] == ("startup", True)
    else:
        assert not any(isinstance(event, tuple) for event in events)
    assert "restart" not in events
    assert "start" not in events
    assert not window.dirty
    assert window.runtime_pending == (state_value == "running")


@pytest.mark.parametrize("state_value", ["stopped", "not_installed", "running"])
def test_apply_restarts_only_running_service(windows, admin_env, qtbot, state_value):
    root, settings, events, state = admin_env
    state["status"] = state_value
    window = windows()
    events.clear()
    window.apply()
    wait_idle(qtbot, window)
    assert ("restart" in events) == (state_value == "running")
    assert "start" not in events
    if state_value == "running":
        assert events.index("save") < events.index(("startup", False)) < events.index("restart")


def test_autostart_failure_rolls_back_exact_file(windows, admin_env, monkeypatch, qtbot, dialogs):
    root, settings, events, state = admin_env
    original = (root / ".env").read_bytes()
    window = windows()
    window.inputs["upstream_host"].setText("new.example.com")
    window.inputs["service_autostart"].setChecked(True)

    def fail_once(enabled):
        events.append(("startup", enabled))
        if enabled:
            state["startup"] = True
            raise OSError("sensitive-password must not leak")
        state["startup"] = False

    monkeypatch.setattr(service_manager, "set_autostart", fail_once)
    window.save()
    wait_idle(qtbot, window)
    assert (root / ".env").read_bytes() == original
    assert state["startup"] is False
    assert window.dirty
    assert "zur\u00fcckgesetzt" in dialogs[-1]
    assert "sensitive-password" not in dialogs[-1]


def test_failed_rollback_shows_scm_truth(windows, admin_env, monkeypatch, qtbot, dialogs):
    root, settings, events, state = admin_env
    window = windows()
    window.inputs["service_autostart"].setChecked(True)

    def fail(enabled):
        state["startup"] = True
        raise OSError()

    monkeypatch.setattr(service_manager, "set_autostart", fail)
    window.save()
    wait_idle(qtbot, window)
    assert window.startup_label.text() == "Ein"
    assert "Abweichung" in window.mismatch_label.text()
    assert "SCM-Rollback fehlgeschlagen" in dialogs[-1]


def test_restart_failure_restores_config_and_restarts_old_service(
    windows, admin_env, monkeypatch, qtbot, dialogs,
):
    root, settings, events, state = admin_env
    state["status"] = "running"
    original = (root / ".env").read_bytes()
    window = windows()
    window.inputs["local_port"].setValue(2525)

    def restart():
        state["status"] = "stopped"
        raise OSError()

    monkeypatch.setattr(service_manager, "restart", restart)
    window.apply()
    wait_idle(qtbot, window)
    assert (root / ".env").read_bytes() == original
    assert state["status"] == "running"
    assert "start" in events
    assert window.dirty
    assert "Laufzeitstand nicht garantiert" in dialogs[-1]


@pytest.mark.parametrize("contents", ["LOCAL_PORT=bad", "UPSTREAM_PASSWORD_B64=!!", "LOCAL_PORT=0"])
def test_invalid_saved_config_shows_defaults_without_overwrite(windows, admin_env, dialogs, contents):
    root, settings, events, state = admin_env
    (root / ".env").write_text(contents)
    window = windows()
    assert window.ready
    assert window.saved is None
    assert window.inputs["local_port"].value() == 25
    assert "Konfiguration ung\u00fcltig" in window.config_error.text()
    assert dialogs
    assert "save" not in events
    assert (root / ".env").read_text() == contents


def test_dirty_refresh_does_not_overwrite_and_close_confirms(
    windows, admin_env, qtbot, monkeypatch,
):
    window = windows()
    window.inputs["upstream_host"].setText("unsaved.example.com")
    window.refresh_status()
    wait_idle(qtbot, window)
    assert window.inputs["upstream_host"].text() == "unsaved.example.com"
    assert window.dirty
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    window.reload_settings()
    assert window.inputs["upstream_host"].text() == "unsaved.example.com"
    assert not window.close()
    assert window.isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    assert window.close()


def test_slow_worker_keeps_ui_responsive_and_refuses_close(windows, qtbot):
    window = windows()
    release = threading.Event()
    threads = []
    ticks = []

    def slow():
        threads.append(QThread.currentThread())
        assert release.wait(5)

    try:
        window._submit(slow)
        retained = window._task
        QTimer.singleShot(0, lambda: ticks.append(True))
        qtbot.waitUntil(lambda: bool(ticks))
        assert window.busy
        assert window._task is retained
        assert not window.close()
        assert window.isVisible()
    finally:
        release.set()
        wait_idle(qtbot, window)
    assert threads[0] != window.thread()
    assert window.close()


def test_connection_uses_draft_settings_off_gui_thread(windows, admin_env, monkeypatch, qtbot):
    window = windows()
    calls = []

    class Client:
        def __init__(self, settings):
            calls.append(settings)

        def test_connection(self):
            calls.append(QThread.currentThread())

    monkeypatch.setattr(gui, "UpstreamClient", Client)
    window.inputs["upstream_host"].setText("draft.example.com")
    window.test_connection()
    wait_idle(qtbot, window)
    assert calls[0].upstream_host == "draft.example.com"
    assert calls[1] != window.thread()


def test_unencrypted_connection_requires_confirmation(windows, admin_env, monkeypatch):
    window = windows()
    client = Mock()
    monkeypatch.setattr(gui, "UpstreamClient", client)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    widget = window.inputs["upstream_security"]
    widget.setCurrentIndex(widget.findData("none"))
    window.test_connection()
    assert not window.busy
    client.assert_not_called()


def test_testmail_uses_local_smtp_without_login(windows, admin_env, monkeypatch, qtbot, dialogs):
    root, settings, events, state = admin_env
    state["status"] = "running"
    window = windows()
    smtp = Mock()
    smtp.return_value.__enter__ = Mock(return_value=smtp)
    smtp.return_value.__exit__ = Mock(return_value=False)
    smtp.send_message.return_value = {}
    monkeypatch.setattr(gui.smtplib, "SMTP", smtp)
    window.recipient.setText("recipient@example.com")
    window.testmail()
    wait_idle(qtbot, window)
    smtp.assert_called_once_with("127.0.0.1", 25, timeout=30)
    smtp.login.assert_not_called()
    message = smtp.send_message.call_args.args[0]
    assert message["To"] == "recipient@example.com"
    assert message["From"] == settings.force_from
    assert "Zustellung ausstehend" in dialogs[-1]


@pytest.mark.parametrize("recipient,sender", [
    ("", "sender@example.com"), ("bad\r\naddress", "sender@example.com"),
    ("a@example.com", ""), ("a@example.com", "bad"),
])
def test_testmail_requires_valid_explicit_addresses(
    windows, admin_env, monkeypatch, dialogs, recipient, sender,
):
    root, settings, events, state = admin_env
    REAL_SAVE(replace(settings, force_from=""), root / ".env")
    window = windows()
    smtp = Mock()
    monkeypatch.setattr(gui.smtplib, "SMTP", smtp)
    window.recipient.setText(recipient)
    window.sender.setText(sender)
    window.testmail()
    assert dialogs
    smtp.assert_not_called()


def test_testmail_rechecks_running_service_in_worker(windows, admin_env, monkeypatch, qtbot, dialogs):
    root, settings, events, state = admin_env
    state["status"] = "running"
    window = windows()
    state["status"] = "stopped"
    smtp = Mock()
    monkeypatch.setattr(gui.smtplib, "SMTP", smtp)
    window.recipient.setText("recipient@example.com")
    window.testmail()
    wait_idle(qtbot, window)
    smtp.assert_not_called()
    assert "laufenden Dienst" in dialogs[-1]


def enqueue(root):
    store = REAL_QUEUE(root / "queue.sqlite3")
    identifier = store.enqueue("sender@example.com", ["recipient@example.com"], b"test",
                               max_bytes=1000)
    store.record(identifier, {"recipient@example.com": ("failed", 0, "failure")})
    return store, identifier


def test_queue_reads_worker_and_retry_resets_age(windows, admin_env, qtbot, monkeypatch):
    root, settings, events, state = admin_env
    store, identifier = enqueue(root)
    with store.connection() as database:
        database.execute("UPDATE messages SET created=1")
    calls = []

    def factory(path):
        calls.append(QThread.currentThread())
        return REAL_QUEUE(path)

    monkeypatch.setattr(gui, "QueueStore", factory)
    window = windows()
    window.refresh_queue()
    wait_idle(qtbot, window)
    assert window.queue.rowCount() == 1
    assert window.queue.columnCount() == 8
    window.queue.selectRow(0)
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args:
                        questions.append(args[2]) or QMessageBox.Yes)
    window.queue_action("retry")
    wait_idle(qtbot, window)
    row = store.list_messages()[0]
    assert row["created"] > 1
    assert row["attempts"] == 0
    assert row["status"] == "pending"
    assert "Aufbewahrungsfrist" in questions[0]
    assert all(thread != window.thread() for thread in calls)


@pytest.mark.parametrize("new_state", ["running", "not_installed", "start_pending", "stop_pending"])
def test_queue_mutation_rechecks_stopped_service(
    windows, admin_env, qtbot, dialogs, new_state,
):
    root, settings, events, state = admin_env
    store, identifier = enqueue(root)
    window = windows()
    window.refresh_queue()
    wait_idle(qtbot, window)
    window.queue.selectRow(0)
    state["status"] = new_state
    window.queue_action("delete")
    wait_idle(qtbot, window)
    assert store.list_messages()
    assert "beendetem Dienst" in dialogs[-1]
    assert "stop" not in events


def test_queue_delete_confirmation(windows, admin_env, monkeypatch, qtbot):
    root, settings, events, state = admin_env
    store, identifier = enqueue(root)
    window = windows()
    window.refresh_queue()
    wait_idle(qtbot, window)
    window.queue.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    window.queue_action("delete")
    assert store.list_messages()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    window.queue_action("delete")
    wait_idle(qtbot, window)
    assert not store.list_messages()


def test_missing_queue_refresh_does_not_create_database(windows, admin_env, qtbot):
    root, settings, events, state = admin_env
    window = windows()
    window.refresh_queue()
    wait_idle(qtbot, window)
    assert not (root / "queue.sqlite3").exists()


def test_logs_bounded_redacted_and_read_in_worker(windows, admin_env, monkeypatch, qtbot):
    root, settings, events, state = admin_env
    (root / "logs").mkdir()
    for name in ("proxy.log", "service.log"):
        (root / "logs" / name).write_bytes(b"discarded-prefix" + b"x" * 70000
                                          + settings.password.encode())
    calls = []
    original = gui.read_logs

    def read(path):
        calls.append(QThread.currentThread())
        return original(path)

    monkeypatch.setattr(gui, "read_logs", read)
    window = windows()
    window.refresh_logs()
    wait_idle(qtbot, window)
    text = window.logs.toPlainText()
    assert "discarded-prefix" not in text
    assert "[redigiert]" in text
    assert settings.password not in text
    assert "proxy.log" in text and "service.log" in text
    assert len(text) < 132000
    assert calls[0] != window.thread()


def test_invalid_initialization_can_be_retried(windows, admin_env, monkeypatch, qtbot, dialogs):
    root, settings, events, state = admin_env

    def fail():
        raise PermissionError()

    monkeypatch.setattr(service_manager, "initialize_data_dir", fail)
    window = windows()
    assert not window.ready
    assert window.buttons["reload"].isEnabled()
    assert not window.buttons["save"].isEnabled()
    monkeypatch.setattr(service_manager, "initialize_data_dir", lambda: root)
    window.reload_settings()
    wait_idle(qtbot, window)
    assert window.ready


def test_actual_license_directory_points_into_internal(monkeypatch, isolated):
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(isolated / "app_gui.exe"))
    assert license_directory_original() == isolated / "_internal" / "licenses"


def test_large_advanced_integers_load_without_clipping(windows, admin_env):
    root, settings, events, state = admin_env
    large = replace(settings, retry_max_seconds=10**14, max_queue_age_hours=10**14,
                    max_message_bytes=10**14, max_queue_bytes=10**15)
    REAL_SAVE(large, root / ".env")
    window = windows()
    assert window._settings() == large


def test_failed_config_write_does_not_touch_scm(windows, admin_env, monkeypatch, qtbot, dialogs):
    root, settings, events, state = admin_env
    original = (root / ".env").read_bytes()
    window = windows()
    events.clear()
    window.inputs["service_autostart"].setChecked(True)

    def fail(*args):
        raise OSError()

    monkeypatch.setattr(config, "save_settings", fail)
    window.save()
    wait_idle(qtbot, window)
    assert not any(isinstance(event, tuple) for event in events)
    assert (root / ".env").read_bytes() == original
    assert window.dirty
    assert "Konfiguration speichern" in dialogs[-1]


def test_failed_file_rollback_reconciles_actual_env_without_overwriting_draft(
    windows, admin_env, monkeypatch, qtbot, dialogs,
):
    root, settings, events, state = admin_env
    window = windows()
    window.inputs["service_autostart"].setChecked(True)
    window.inputs["upstream_host"].setText("draft.example.com")

    def fail_startup(enabled):
        state["startup"] = False
        if enabled:
            raise OSError()

    def fail_restore(*args):
        raise OSError()

    monkeypatch.setattr(service_manager, "set_autostart", fail_startup)
    monkeypatch.setattr(gui, "restore_config", fail_restore)
    window.save()
    wait_idle(qtbot, window)
    assert window.saved == REAL_LOAD(root / ".env")
    assert window.saved.service_autostart
    assert window.startup_label.text() == "Aus"
    assert "Abweichung" in window.mismatch_label.text()
    assert "Konfigurations-Rollback fehlgeschlagen" in dialogs[-1]
    assert window.inputs["upstream_host"].text() == "draft.example.com"
    assert window.dirty


def test_external_config_change_refresh_preserves_draft(windows, admin_env, qtbot):
    root, settings, events, state = admin_env
    state["status"] = "running"
    window = windows()
    window.inputs["upstream_host"].setText("draft.example.com")
    external = replace(settings, upstream_host="external.example.com", service_autostart=True)
    REAL_SAVE(external, root / ".env")
    window.refresh_status()
    wait_idle(qtbot, window)
    assert window.saved == external
    assert window.inputs["upstream_host"].text() == "draft.example.com"
    assert window.dirty
    assert window.runtime_pending
    assert "Abweichung" in window.mismatch_label.text()


@pytest.mark.parametrize("state_value", ["start_pending", "stop_pending", "delete_pending"])
def test_save_during_scm_transition_refuses_writes(windows, admin_env, qtbot, state_value):
    root, settings, events, state = admin_env
    state["status"] = state_value
    original = (root / ".env").read_bytes()
    window = windows()
    events.clear()
    window.inputs["local_port"].setValue(2525)
    window.save()
    wait_idle(qtbot, window)
    assert "save" not in events
    assert not any(isinstance(event, tuple) for event in events)
    assert (root / ".env").read_bytes() == original


def test_invalid_config_can_only_be_replaced_by_explicit_save(windows, admin_env, qtbot):
    root, settings, events, state = admin_env
    (root / ".env").write_text("UPSTREAM_PASSWORD_B64=!!")
    window = windows()
    window.inputs["upstream_host"].setText(settings.upstream_host)
    window.inputs["upstream_username"].setText(settings.upstream_username)
    window.inputs["password"].setText(settings.password)
    assert (root / ".env").read_text() == "UPSTREAM_PASSWORD_B64=!!"
    window.save()
    wait_idle(qtbot, window)
    REAL_LOAD(root / ".env").validate()
    assert not window.config_error.text()


def test_status_error_shows_unknown_not_stale_success(windows, admin_env, monkeypatch, qtbot, dialogs):
    root, settings, events, state = admin_env
    state["status"] = "running"
    window = windows()

    def fail():
        raise PermissionError()

    monkeypatch.setattr(service_manager, "status", fail)
    window.refresh_status()
    wait_idle(qtbot, window)
    assert window.state == "unknown"
    assert window.startup_label.text() == "Unbekannt"
    assert not window.buttons["restart"].isEnabled()
    assert not dialogs


def test_install_sets_saved_startup_and_does_not_start(windows, admin_env, qtbot):
    root, settings, events, state = admin_env
    REAL_SAVE(replace(settings, service_autostart=True), root / ".env")
    state["status"] = "not_installed"
    window = windows()
    events.clear()
    window.service_action("install")
    wait_idle(qtbot, window)
    assert events[:2] == ["install", ("startup", True)]
    assert "start" not in events
    assert state["status"] == "stopped"


def test_remove_requires_confirmation(windows, admin_env, monkeypatch):
    root, settings, events, state = admin_env
    window = windows()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    window.service_action("remove")
    assert "remove" not in events


def test_dirty_close_stops_timer_during_confirmation(windows, admin_env, monkeypatch):
    window = windows()
    window.inputs["upstream_host"].setText("draft.example.com")
    window.timer.start()

    def decline(text):
        assert not window.timer.isActive()
        return False

    monkeypatch.setattr(window, "_confirm", decline)
    assert not window.close()
    assert window.timer.isActive()


def test_config_and_scm_reads_use_worker_thread(windows, admin_env, monkeypatch):
    root, settings, events, state = admin_env
    calls = []

    def status():
        calls.append(QThread.currentThread())
        return "stopped"

    def load(path):
        calls.append(QThread.currentThread())
        return REAL_LOAD(path)

    monkeypatch.setattr(service_manager, "status", status)
    monkeypatch.setattr(config, "load_settings", load)
    window = windows()
    assert len(calls) >= 2
    assert all(thread != window.thread() for thread in calls)


@pytest.mark.parametrize("argument", ["--help", "--smoke-test"])
def test_gui_cli_runs_without_production_data(argument, isolated):
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PROGRAMDATA=str(isolated), QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, str(root / "app_gui.py"), argument],
                            capture_output=True, text=True, timeout=30, env=environment,
                            cwd=isolated)
    assert result.returncode == 0, result.stderr
    assert not list(isolated.iterdir())
    if argument == "--help":
        assert "--smoke-test" in result.stdout


license_directory_original = gui.license_directory
import base64
import os
import smtplib
import ssl
import sys
import tempfile
from dataclasses import fields
from datetime import datetime
from email.errors import HeaderParseError
from email.message import EmailMessage
from pathlib import Path

from PySide6.QtCore import QRegularExpression, QThread, QTimer, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QStyle, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from smtp_proxy import config
from smtp_proxy.certificates import certificate_paths
from smtp_proxy.paths import application_dir, data_dir, service_settings_path
from smtp_proxy.portable import idle_runtime, runtime_status
from smtp_proxy.queue_store import QueueStore
from smtp_proxy.relay import UpstreamClient, safe_error

if sys.platform == "win32":
    from smtp_proxy import service_manager
else:
    service_manager = None


STATES = {
    "stopped": "Beendet", "running": "Wird ausgefuehrt",
    "not_installed": "Nicht installiert", "start_pending": "Wird gestartet",
    "stop_pending": "Wird beendet", "delete_pending": "Wird entfernt",
    "paused": "Angehalten", "pause_pending": "Wird angehalten",
    "continue_pending": "Wird fortgesetzt", "unknown": "Unbekannt",
}


class OperationError(Exception):
    pass


class Task(QThread):
    completed = Signal(object, object)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except Exception as error:
            self.completed.emit(None, str(error) if isinstance(error, OperationError)
                                else safe_error(error))
        else:
            self.completed.emit(result, None)


def service_snapshot(admin):
    state = service_manager.status()
    startup = (service_manager.autostart_enabled()
               if admin and state not in {"not_installed", "delete_pending"} else None)
    return state, startup


def read_settings(root, path=None):
    try:
        path = path or root / ".env"
        settings = config.load_settings(path)
        exists = path.exists()
        if exists:
            settings.validate()
    except (ValueError, HeaderParseError, OSError) as error:
        return config.Settings(), f"Konfiguration ung\u00fcltig: {safe_error(error)}", False
    return settings, "", exists


def initialize_settings():
    root = service_manager.initialize_data_dir()
    path = service_settings_path(root)
    if path == application_dir() / ".env" and path.exists():
        service_manager.verify_settings_file(path)
    return root, *read_settings(root, path)


def observe_portable_status(root):
    settings, error, valid = read_settings(root)
    return (runtime_status(root), None), settings if valid else None, error


def persist_portable_settings(settings, root):
    config.save_settings(settings, root / ".env")
    state = runtime_status(root)
    return (state, None), state == "running"


def mutate_portable_queue(root, action, identifier):
    if action not in {"retry", "delete"}:
        raise OperationError("Ungueltige Queue-Aktion.")
    with idle_runtime(root):
        path = root / "queue.sqlite3"
        if not path.is_file():
            raise OperationError("Keine Warteschlange vorhanden.")
        store = QueueStore(path)
        getattr(store, action)(identifier)
        return store.list_messages()


def observe_status(admin, root):
    snapshot = service_snapshot(admin)
    if root is None:
        return snapshot, None, ""
    settings, error, valid = read_settings(root, service_settings_path(root))
    return snapshot, settings if valid else None, error


def restore_config(path, original, *, protect=None):
    if original is None:
        path.unlink(missing_ok=True)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".env-rollback-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        if protect is not None:
            protect(Path(temporary))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def persist_settings(settings, *, restart=False):
    settings.validate()
    service_manager.initialize_data_dir()
    path = application_dir() / ".env"
    original = path.read_bytes() if path.exists() else None
    state, startup = service_snapshot(True)
    if state not in {"running", "stopped", "not_installed"}:
        raise OperationError("Dienst ist im \u00dcbergang. Bitte sp\u00e4ter erneut versuchen.")
    stage = "Konfiguration speichern"
    startup_attempted = False
    try:
        config.save_settings(settings, path, protect=service_manager.protect_settings_file)
        if startup is not None:
            stage = "SCM-Autostart setzen"
            startup_attempted = True
            service_manager.set_autostart(settings.service_autostart)
        if restart and state == "running":
            stage = "Dienst neu starten"
            service_manager.restart()
    except Exception as error:
        recovery = []
        try:
            restore_config(path, original, protect=service_manager.protect_settings_file)
        except Exception as rollback_error:
            recovery.append(f"Konfigurations-Rollback fehlgeschlagen ({safe_error(rollback_error)})")
        if startup_attempted:
            try:
                service_manager.set_autostart(startup)
            except Exception as rollback_error:
                recovery.append(f"SCM-Rollback fehlgeschlagen ({safe_error(rollback_error)})")
        if restart and state == "running" and not recovery:
            try:
                if service_manager.status() == "stopped":
                    service_manager.start()
            except Exception as rollback_error:
                recovery.append(f"Wiederanlauf fehlgeschlagen ({safe_error(rollback_error)})")
        detail = "; ".join(recovery) if recovery else "Datei und Startmodus zur\u00fcckgesetzt"
        raise OperationError(
            f"{stage}: {safe_error(error)}. {detail}. "
            "SCM-Status und laufende Konfiguration pr\u00fcfen; Laufzeitstand nicht garantiert."
        ) from None
    return service_snapshot(True), state == "running" and not restart


def read_queue(root):
    path = root / "queue.sqlite3"
    return QueueStore(path).list_messages() if path.is_file() else []


def mutate_queue(root, action, identifier):
    if service_manager.status() != "stopped":
        raise OperationError("Nur bei beendetem Dienst erlaubt. Dienst zuerst explizit beenden.")
    path = root / "queue.sqlite3"
    if not path.is_file():
        raise OperationError("Keine Warteschlange vorhanden.")
    store = QueueStore(path)
    getattr(store, action)(identifier)
    return store.list_messages()


def read_logs(root):
    parts = []
    for name in ("proxy.log", "service.log"):
        path = root / "logs" / name
        try:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 65536))
                text = stream.read(65536).decode("utf-8", errors="replace")
        except FileNotFoundError:
            text = "Noch kein Protokoll vorhanden."
        parts.append(f"=== {name} ===\n{text}")
    return "\n\n".join(parts)


def send_testmail(settings, recipient, sender, *, portable=False, directory=None):
    recipient = config.validate_address(recipient)
    sender = config.validate_address(settings.force_from or sender)
    if not portable and service_manager.status() != "running":
        raise OperationError("Testmail erfordert einen laufenden Dienst.")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "SMTPProxy Testmail"
    message.set_content("Testnachricht von SMTPProxy.")
    arguments = {"timeout": settings.smtp_timeout_seconds}
    context = None
    if settings.local_security != "none":
        certificate_path, key_path = certificate_paths(settings, directory or data_dir())
        context = ssl.create_default_context(cafile=str(certificate_path))
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    smtp_class = smtplib.SMTP
    if settings.local_security == "tls":
        smtp_class = smtplib.SMTP_SSL
        arguments["context"] = context
    with smtp_class("127.0.0.1", settings.local_port, **arguments) as client:
        if settings.local_security == "starttls":
            client.starttls(context=context)
        refused = client.send_message(message, from_addr=sender, to_addrs=[recipient])
        if refused:
            raise OperationError("Der lokale Proxy hat den Empf\u00e4nger abgelehnt.")


def license_directory():
    root = (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[3])
    return root / "_internal" / "licenses"


class MainWindow(QMainWindow):
    def __init__(self, *, read_only: bool = False, auto_refresh: bool = True, directory=None):
        super().__init__()
        self.portable_root = Path(directory).expanduser().resolve() if directory is not None else None
        self.portable = self.portable_root is not None
        self.admin = not read_only and (self.portable or service_manager.is_admin())
        self.ready = False
        self.dirty = False
        self.saved = None
        self.root = None
        self.runtime_pending = False
        self.state = "unknown"
        self._task = None
        self._callback = None
        self._refresh_after = False
        self._loading = False
        self._operation_error = ""
        self._status_query = False
        self.controls = []
        self.inputs = {}
        self.buttons = {}
        self.setWindowTitle("SMTPProxy")
        self.resize(820, 720)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_status()
        if self.admin:
            self._build_settings()
            self._build_queue()
            self._build_logs()
        else:
            for title in ("Einstellungen", "Warteschlange", "Protokoll"):
                self.tabs.addTab(QWidget(), title)
                self.tabs.setTabEnabled(self.tabs.count() - 1, False)
        self._build_about()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh_status)
        self._update_controls()
        if auto_refresh:
            self.timer.start()
            if self.admin:
                self.reload_settings(initial=True)
            else:
                self.refresh_status()

    @property
    def busy(self):
        return self._task is not None

    def _page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        self.tabs.addTab(page, title)
        return layout

    def _button(self, key, text, icon, callback, layout, *, admin=False):
        button = QPushButton(self.style().standardIcon(icon), text)
        button.setToolTip(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.buttons[key] = button
        self.controls.append((button, admin))
        return button

    def _build_status(self):
        layout = self._page("Status")
        form = QFormLayout()
        self.mode_label = QLabel("Portabel" if self.portable else
                    "Verwaltung" if self.admin else "Nur Dienststatus")
        self.state_label = QLabel("Wird abgefragt ...")
        self.startup_label = QLabel("Noch nicht abgefragt" if self.admin else "-")
        self.mismatch_label = QLabel()
        self.mismatch_label.setWordWrap(True)
        form.addRow("Zugriff", self.mode_label)
        form.addRow("Proxy (CLI)" if self.portable else "Dienst", self.state_label)
        if not self.portable:
            form.addRow("Autostart (SCM)", self.startup_label)
        if self.portable:
            path_label = QLabel(str(self.portable_root))
            path_label.setWordWrap(True)
            form.addRow("Datenverzeichnis", path_label)
        layout.addLayout(form)
        layout.addWidget(self.mismatch_label)
        row = QHBoxLayout()
        self._button("refresh_status", "Aktualisieren", QStyle.SP_BrowserReload,
                     self.refresh_status, row)
        if not self.admin:
            self._button("elevate", "Als Administrator \u00f6ffnen", QStyle.SP_VistaShield,
                         self.elevate, row)
        row.addStretch()
        layout.addLayout(row)
        if self.admin:
            row = QHBoxLayout()
            for key, text, icon in (
                ("install", "Installieren", QStyle.SP_DriveHDIcon),
                ("start", "Starten", QStyle.SP_MediaPlay),
                ("stop", "Beenden", QStyle.SP_MediaStop),
                ("restart", "Neu starten", QStyle.SP_BrowserReload),
                ("remove", "Entfernen", QStyle.SP_TrashIcon),
            ):
                if not self.portable:
                    self._button(key, text, icon, lambda checked=False, action=key:
                                 self.service_action(action), row, admin=True)
            row.addStretch()
            layout.addLayout(row)
            layout.addSpacing(16)
            mail_form = QFormLayout()
            self.recipient = QLineEdit()
            self.sender = QLineEdit()
            self.sender.setToolTip("Erforderlich, wenn keine feste Absenderadresse gesetzt ist.")
            mail_form.addRow("Testmail an", self.recipient)
            mail_form.addRow("Testmail von", self.sender)
            layout.addLayout(mail_form)
            row = QHBoxLayout()
            self._button("testmail", "Testmail senden", QStyle.SP_ArrowForward,
                         self.testmail, row, admin=True)
            row.addStretch()
            layout.addLayout(row)
        layout.addStretch()

    def _build_settings(self):
        layout = self._page("Einstellungen")
        self.config_error = QLabel()
        self.config_error.setWordWrap(True)
        layout.addWidget(self.config_error)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        form = QFormLayout(container)
        labels = {
            "local_port": "Lokaler Port", "upstream_host": "Zielserver",
            "allowed_hosts": "Erlaubte Client-Hosts",
            "local_security": "Empfang: Transportsicherheit",
            "local_certificate": "Empfang: Zertifikat (PEM)",
            "local_private_key": "Empfang: Privater Schluessel (PEM)",
            "upstream_port": "Zielport", "upstream_security": "Transportsicherheit",
            "upstream_accept_invalid_certificate": "Zertifikatspruefung",
            "upstream_username": "Benutzername", "password": "Kennwort",
            "force_from": "Fester Absender", "service_autostart": "Dienst-Autostart",
            "smtp_timeout_seconds": "SMTP-Timeout (s)",
            "retry_initial_seconds": "Erster Wiederholungsabstand (s)",
            "retry_max_seconds": "Max. Wiederholungsabstand (s)",
            "max_queue_age_hours": "Aufbewahrungsfrist (h)",
            "max_message_bytes": "Nachrichtenlimit (Byte)",
            "max_queue_bytes": "Warteschlangenlimit (Byte)",
        }
        defaults = config.Settings()
        for field in fields(defaults):
            name = field.name
            default = getattr(defaults, name)
            if name == "smtp_timeout_seconds":
                form.addRow(QLabel("Erweitert"))
            if name in {"upstream_security", "local_security"}:
                widget = QComboBox()
                for text, value in (("STARTTLS", "starttls"), ("TLS", "tls"),
                                    ("Ohne TLS", "none")):
                    widget.addItem(text, value)
                widget.setToolTip(
                    "Ohne TLS werden SMTP-Zugangsdaten unverschl\u00fcsselt \u00fcbertragen."
                    if name == "upstream_security" else
                    "STARTTLS ist verpflichtend; TLS verschluesselt bereits den Verbindungsaufbau."
                )
                widget.currentIndexChanged.connect(self._mark_dirty)
            elif isinstance(default, bool):
                text = ("Ungueltige TLS-Zertifikate immer akzeptieren (unsicher)"
                        if name == "upstream_accept_invalid_certificate" else
                        "Automatisch starten")
                widget = QCheckBox(text)
                if name == "upstream_accept_invalid_certificate":
                    widget.setToolTip(
                        "Deaktiviert Zertifikatsketten- und Hostnamenpruefung. "
                        "Verbindungen koennen dadurch von Dritten abgefangen werden."
                    )
                widget.toggled.connect(self._mark_dirty)
            elif name in {"local_port", "upstream_port", "smtp_timeout_seconds"}:
                widget = QSpinBox()
                maximum = 65535 if name.endswith("port") else 2147483647
                if name == "smtp_timeout_seconds":
                    maximum = 120
                widget.setRange(1, maximum)
                widget.valueChanged.connect(self._mark_dirty)
            else:
                widget = QLineEdit()
                if isinstance(default, int):
                    widget.setValidator(QRegularExpressionValidator(
                        QRegularExpression("[0-9]+"), widget))
                widget.textChanged.connect(self._mark_dirty)
            widget.setObjectName(name)
            if name == "allowed_hosts":
                widget.setToolTip("Kommagetrennte Quell-IP-Adressen, CIDR-Netze oder DNS-Namen.")
            elif name in {"local_certificate", "local_private_key"}:
                widget.setToolTip(
                    "Beide Felder leer: selbstsigniertes Zertifikat automatisch im Datenverzeichnis. "
                    "Eigene PEM-Dateien: absolute, fuer das ausfuehrende Konto lesbare Pfade."
                )
            self.inputs[name] = widget
            if name == "password":
                widget.setEchoMode(QLineEdit.Password)
                widget.setToolTip("Base64 ist keine Verschl\u00fcsselung. Speicherung in gesch\u00fctzter .env.")
                row = QHBoxLayout()
                row.addWidget(widget)
                self.show_password = QCheckBox("Anzeigen")
                self.show_password.toggled.connect(lambda checked: self.inputs["password"].setEchoMode(
                    QLineEdit.Normal if checked else QLineEdit.Password))
                row.addWidget(self.show_password)
                form.addRow(labels[name], row)
            else:
                form.addRow(labels[name], widget)
            if name == "service_autostart" and self.portable:
                widget.setVisible(False)
                form.labelForField(widget).setVisible(False)
        scroll.setWidget(container)
        layout.addWidget(scroll)
        row = QHBoxLayout()
        for key, text, icon, callback in (
            ("reload", "Neu laden", QStyle.SP_BrowserReload, self.reload_settings),
            ("save", "Speichern", QStyle.SP_DialogSaveButton, self.save),
            ("apply", "Speichern + neu starten", QStyle.SP_BrowserReload, self.apply),
            ("connection", "Verbindung testen", QStyle.SP_DialogApplyButton, self.test_connection),
        ):
            self._button(key, text, icon, callback, row, admin=True)
        row.addStretch()
        layout.addLayout(row)
        if self.portable:
            self.buttons["apply"].hide()
        self._populate(defaults)

    def _build_queue(self):
        layout = self._page("Warteschlange")
        self.queue = QTableWidget(0, 8)
        self.queue.setHorizontalHeaderLabels([
            "ID", "Datum", "Absender", "Adresse", "Status", "Versuche", "Fehler", "Gr\u00f6\u00dfe",
        ])
        self.queue.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue.setSelectionMode(QAbstractItemView.SingleSelection)
        self.queue.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.queue.horizontalHeader().setStretchLastSection(True)
        self.queue.itemSelectionChanged.connect(self._update_controls)
        layout.addWidget(self.queue)
        row = QHBoxLayout()
        self._button("queue_refresh", "Aktualisieren", QStyle.SP_BrowserReload,
                     self.refresh_queue, row, admin=True)
        self._button("retry", "Wiederholen", QStyle.SP_BrowserReload,
                     lambda: self.queue_action("retry"), row, admin=True)
        self._button("delete", "L\u00f6schen", QStyle.SP_TrashIcon,
                     lambda: self.queue_action("delete"), row, admin=True)
        row.addStretch()
        layout.addLayout(row)

    def _build_logs(self):
        layout = self._page("Protokoll")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(4000)
        layout.addWidget(self.logs)
        row = QHBoxLayout()
        self._button("logs_refresh", "Aktualisieren", QStyle.SP_BrowserReload,
                     self.refresh_logs, row, admin=True)
        row.addStretch()
        layout.addLayout(row)

    def _build_about(self):
        layout = self._page("About")
        for text in ("SMTPProxy", "Version 1.0.0", "Marcel Braun", "marcel.braun@ferchau.com"):
            layout.addWidget(QLabel(text))
        path = QLabel(str(license_directory()))
        path.setWordWrap(True)
        layout.addWidget(path)
        layout.addStretch()

    def _submit(self, operation, callback=None, *, refresh_after=False, status_query=False):
        if self.busy:
            return False
        self._callback = callback
        self._refresh_after = refresh_after
        self._status_query = status_query
        self._task = Task(operation, self)
        self._task.completed.connect(self._completed)
        self._task.finished.connect(self._finished)
        self._update_controls()
        self.statusBar().showMessage("Vorgang l\u00e4uft ...")
        self._task.start()
        return True

    def _completed(self, result, error):
        if error:
            self._operation_error = self._redact(error)
            self.mismatch_label.setText(self._operation_error)
            self.statusBar().showMessage("Vorgang fehlgeschlagen")
            if self._status_query:
                self.state = "unknown"
                self.state_label.setText("Status nicht verf\u00fcgbar")
                self.startup_label.setText("Unbekannt")
            else:
                QMessageBox.warning(self, "SMTPProxy", self._redact(error))
        elif self._callback:
            self._callback(result)

    def _finished(self):
        task = self._task
        task.wait()
        self._task = None
        self._callback = None
        refresh = self._refresh_after
        self._refresh_after = False
        task.deleteLater()
        self._update_controls()
        if refresh:
            self.refresh_status()
        elif self.statusBar().currentMessage() == "Vorgang l\u00e4uft ...":
            self.statusBar().showMessage("Bereit")

    def _update_controls(self):
        for button, admin in self.controls:
            button.setEnabled(not self.busy and (not admin or self.ready))
        if not self.admin:
            return
        self.buttons["reload"].setEnabled(not self.busy)
        settings_busy = self.busy and not self._status_query
        for widget in self.inputs.values():
            widget.setEnabled(self.ready and not settings_busy)
        if self.portable:
            self.inputs["service_autostart"].setEnabled(False)
            self.buttons["apply"].setEnabled(False)
        if hasattr(self, "show_password"):
            self.show_password.setEnabled(self.ready and not settings_busy)
        states = {"install": {"not_installed"}, "start": {"stopped"},
                  "stop": {"running", "paused"}, "restart": {"running"},
                  "remove": {"running", "stopped", "paused"}, "testmail": {"running"}}
        for key, allowed in states.items():
            if key in self.buttons:
                self.buttons[key].setEnabled(self.ready and not self.busy and self.state in allowed)
        self.buttons["testmail"].setEnabled(
            self.buttons["testmail"].isEnabled() and self.saved is not None
            and not self.dirty and not self.runtime_pending)
        if hasattr(self, "queue"):
            selected = self.queue.currentRow() >= 0
            for key in ("retry", "delete"):
                self.buttons[key].setEnabled(
                    self.ready and not self.busy and selected and self.state == "stopped")

    def refresh_status(self):
        if not self.busy:
            if self.portable:
                root = self.portable_root
                self._submit(lambda: observe_portable_status(root), self._observed, status_query=True)
                return
            root = self.root if self.admin and self.ready else None
            admin = self.admin
            self._submit(lambda: observe_status(admin, root), self._observed, status_query=True)

    def _observed(self, result):
        snapshot, persisted, error = result
        if self.admin and self.ready:
            if snapshot[0] == "running" and self.saved != persisted:
                self.runtime_pending = True
            elif snapshot[0] == "stopped":
                self.runtime_pending = False
            self.saved = persisted
            self.config_error.setText(error)
        self._show_status(snapshot)

    def _show_status(self, snapshot):
        self.state, startup = snapshot
        self.state_label.setText(STATES.get(self.state, self.state))
        self.startup_label.setText("-" if startup is None else "Ein" if startup else "Aus")
        notices = []
        if self.saved and startup is not None and startup != self.saved.service_autostart:
            notices.append("Abweichung: gespeicherter Autostart entspricht nicht dem SCM-Startmodus.")
        if self.runtime_pending:
            notices.append("CLI mit diesem Datenverzeichnis neu starten." if self.portable else
                           "Gespeicherte Einstellungen noch nicht durch Neustart angewendet.")
        if self._operation_error:
            notices.append(self._operation_error)
        self.mismatch_label.setText("\n".join(notices))

    def elevate(self):
        self._submit(service_manager.elevate_gui,
                     lambda result: self.statusBar().showMessage("Administratorfenster angefordert."))

    def reload_settings(self, checked=False, *, initial=False):
        if not self.admin or self.busy:
            return
        if self.dirty and not self._confirm("Ungespeicherte Einstellungen verwerfen?"):
            return
        if self.portable:
            root = self.portable_root
            self._submit(lambda: (root, *read_settings(root)), self._settings_loaded, refresh_after=True)
        else:
            self._submit(initialize_settings, self._settings_loaded, refresh_after=True)

    def _settings_loaded(self, result):
        self.root, settings, error, valid = result
        self.ready = True
        self.saved = settings if valid else None
        self._operation_error = ""
        self._populate(settings)
        self.config_error.setText(error)
        if error:
            QMessageBox.warning(self, "Konfiguration", error + ". Standardwerte geladen; "
                                "Datei wird nur durch ausdr\u00fcckliches Speichern ersetzt.")

    def _populate(self, settings):
        self._loading = True
        for name, widget in self.inputs.items():
            value = getattr(settings, name)
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(widget.findData(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value)
            elif isinstance(widget, QSpinBox):
                widget.setValue(value)
            else:
                widget.setText(str(value))
        self._loading = False
        self.dirty = False
        self.setWindowModified(False)

    def _mark_dirty(self, *args):
        if not self._loading:
            self.dirty = True
            self._update_controls()

    def _settings(self):
        values = {}
        defaults = config.Settings()
        for name, widget in self.inputs.items():
            if isinstance(widget, QComboBox):
                values[name] = widget.currentData()
            elif isinstance(widget, QCheckBox):
                values[name] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                values[name] = widget.value()
            else:
                text = widget.text()
                values[name] = int(text) if isinstance(getattr(defaults, name), int) else text
        settings = config.Settings(**values)
        settings.validate()
        return settings

    def _validated_settings(self):
        try:
            settings = self._settings()
        except (ValueError, HeaderParseError) as error:
            message = "Ung\u00fcltige E-Mail-Adresse." if isinstance(error, HeaderParseError) else str(error)
            QMessageBox.warning(self, "Einstellungen", message)
            return None
        if settings.upstream_security == "none" and not self._confirm(
            "Ohne TLS werden Zugangsdaten unverschl\u00fcsselt \u00fcbertragen. Fortfahren?"
        ):
            return None
        return settings

    def save(self):
        self._save(False)

    def apply(self):
        if self.portable:
            return
        if self._confirm("Einstellungen speichern und einen laufenden Dienst neu starten?"):
            self._save(True)

    def _save(self, restart):
        if not self.admin or not self.ready or self.busy:
            return
        settings = self._validated_settings()
        if settings is None:
            return
        if self.portable:
            root = self.root
            self._submit(lambda: persist_portable_settings(settings, root),
                         lambda result: self._saved(settings, result), refresh_after=True)
            return
        self._submit(lambda: persist_settings(settings, restart=restart),
                     lambda result: self._saved(settings, result), refresh_after=True)

    def _saved(self, settings, result):
        snapshot, self.runtime_pending = result
        self.saved = settings
        self._operation_error = ""
        self.dirty = False
        self.config_error.clear()
        self._show_status(snapshot)
        self.statusBar().showMessage("Gespeichert" + (
            "; Neustart noch erforderlich." if self.runtime_pending else "."))

    def service_action(self, action):
        if self.portable or not self.admin or not self.ready or self.busy:
            return
        if action in {"install", "start", "restart"} and (self.dirty or self.saved is None):
            QMessageBox.warning(self, "Dienst", "Zuerst g\u00fcltige Einstellungen speichern.")
            return
        if action in {"stop", "restart", "remove"} and not self._confirm(
            {"stop": "Dienst beenden?", "restart": "Dienst neu starten?",
             "remove": "Dienst entfernen? Gespeicherte Daten bleiben erhalten."}[action]
        ):
            return
        settings = self.saved

        def operation():
            getattr(service_manager, action)()
            if action == "install":
                service_manager.set_autostart(settings.service_autostart)
            return service_snapshot(True)

        def done(snapshot):
            if action in {"start", "restart"}:
                self.runtime_pending = False
            self._show_status(snapshot)
            self.statusBar().showMessage("Dienstaktion abgeschlossen.")

        self._submit(operation, done, refresh_after=True)

    def test_connection(self):
        if not self.admin or not self.ready or self.busy:
            return
        settings = self._validated_settings()
        if settings:
            self._submit(lambda: UpstreamClient(settings).test_connection(),
                         lambda result: QMessageBox.information(
                             self, "Verbindung", "SMTP-Anmeldung erfolgreich."))

    def testmail(self):
        if not self.admin or self.busy or self.saved is None:
            return
        if self.dirty or self.runtime_pending:
            QMessageBox.warning(self, "Testmail", "Zuerst speichern und Einstellungen anwenden.")
            return
        try:
            recipient = config.validate_address(self.recipient.text())
            sender = config.validate_address(self.saved.force_from or self.sender.text())
        except (ValueError, HeaderParseError):
            QMessageBox.warning(self, "Testmail", "G\u00fcltige Empf\u00e4nger- und Absenderadresse erforderlich.")
            return
        if not self._confirm(f"Testmail an {recipient} \u00fcber den lokalen Proxy senden?"):
            return
        settings = self.saved

        def operation():
            return send_testmail(
                settings, recipient, sender, portable=self.portable, directory=self.root,
            )

        self._submit(operation,
                     lambda result: QMessageBox.information(
                         self, "Testmail", "Vom lokalen Proxy angenommen; Zustellung ausstehend."),
                     refresh_after=True)

    def refresh_queue(self):
        if self.admin and self.ready:
            root = self.root
            self._submit(lambda: read_queue(root), self._show_queue)

    def _tab_changed(self, index):
        if self.admin and self.ready and not self.busy:
            if index == 2:
                self.refresh_queue()
            elif index == 3:
                self.refresh_logs()

    def _show_queue(self, rows):
        self.queue.setRowCount(len(rows))
        for index, row in enumerate(rows):
            created = datetime.fromtimestamp(row["created"]).strftime("%d.%m.%Y %H:%M:%S")
            values = [row["id"], created, row["sender"], row["address"],
                      {"pending": "Ausstehend", "failed": "Fehlgeschlagen", "sent": "Gesendet"}
                      .get(row["status"], row["status"]), row["attempts"],
                      self._redact(row["error"]), f'{row["size"]} B']
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.queue.setItem(index, column, item)

    def queue_action(self, action):
        if not self.admin or not self.ready or self.busy:
            return
        selected = self.queue.currentRow()
        if selected < 0:
            return
        if self.state != "stopped":
            QMessageBox.warning(self, "Warteschlange", "CLI zuerst beenden." if self.portable else
                                "Dienst zuerst explizit beenden.")
            return
        question = ("Nachricht wiederholen? Alter und Versuche werden zur\u00fcckgesetzt; "
                    "die Aufbewahrungsfrist beginnt erneut." if action == "retry" else
                "Nachricht f\u00fcr alle Empf\u00e4nger unwiderruflich l\u00f6schen?")
        if not self._confirm(question):
            return
        identifier = self.queue.item(selected, 0).text()
        root = self.root
        operation = mutate_portable_queue if self.portable else mutate_queue
        self._submit(lambda: operation(root, action, identifier), self._show_queue,
                     refresh_after=True)

    def refresh_logs(self):
        if self.admin and self.ready:
            root = self.root
            self._submit(lambda: read_logs(root),
                         lambda text: self.logs.setPlainText(self._redact(text)))

    def _redact(self, text):
        secrets = [self.saved.password] if self.saved else []
        if "password" in self.inputs:
            secrets.append(self.inputs["password"].text())
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redigiert]")
                text = text.replace(base64.b64encode(secret.encode()).decode(), "[redigiert]")
        return text

    def _confirm(self, text):
        return QMessageBox.question(self, "SMTPProxy", text,
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def closeEvent(self, event):
        if self.busy:
            self.statusBar().showMessage("Vorgang l\u00e4uft. Bitte vor dem Schlie\u00dfen abwarten.")
            event.ignore()
            return
        was_active = self.timer.isActive()
        self.timer.stop()
        if self.dirty and not self._confirm("Ungespeicherte Einstellungen verwerfen und schlie\u00dfen?"):
            if was_active:
                self.timer.start()
            event.ignore()
            return
        event.accept()
import base64
import binascii
import io
import os
import socket
import tempfile
from dataclasses import dataclass, field, fields
from email.headerregistry import Address
from pathlib import Path

from dotenv import dotenv_values

from smtp_proxy.paths import data_dir


@dataclass(frozen=True)
class Settings:
    local_port: int = 25
    upstream_host: str = ""
    upstream_port: int = 587
    upstream_security: str = "starttls"
    upstream_username: str = ""
    password: str = field(default="", repr=False)
    force_from: str = ""
    service_autostart: bool = False
    smtp_timeout_seconds: int = 30
    retry_initial_seconds: int = 30
    retry_max_seconds: int = 3600
    max_queue_age_hours: int = 120
    max_message_bytes: int = 26214400
    max_queue_bytes: int = 1073741824

    def validate(self, *, resolve: bool = False) -> None:
        for port in (self.local_port, self.upstream_port):
            if not 1 <= port <= 65535:
                raise ValueError("Ports muessen zwischen 1 und 65535 liegen.")
        if self.upstream_security not in {"none", "starttls", "tls"}:
            raise ValueError("Unbekannter TLS-Modus.")
        if not self.upstream_host or any(char.isspace() for char in self.upstream_host):
            raise ValueError("Ein gueltiger Zielserver ist erforderlich.")
        if any(char in self.upstream_host for char in "/\\\x00"):
            raise ValueError("Zielserver darf keinen Pfad enthalten.")
        if not self.upstream_username or not self.password:
            raise ValueError("Benutzername und Kennwort sind erforderlich.")
        if any(char in self.upstream_username for char in "\r\n\x00"):
            raise ValueError("Ungueltiger Benutzername.")
        if self.force_from:
            validate_address(self.force_from)
        for name in (
            "smtp_timeout_seconds", "retry_initial_seconds", "retry_max_seconds",
            "max_queue_age_hours", "max_message_bytes", "max_queue_bytes",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} muss positiv sein.")
        if self.smtp_timeout_seconds > 120:
            raise ValueError("SMTP-Timeout darf hoechstens 120 Sekunden betragen.")
        if self.retry_initial_seconds > self.retry_max_seconds:
            raise ValueError("Initialer Retry-Abstand darf Maximum nicht uebersteigen.")
        if self.max_message_bytes > self.max_queue_bytes:
            raise ValueError("Nachrichtenlimit darf Queue-Limit nicht uebersteigen.")
        if self.local_port == self.upstream_port:
            host = self.upstream_host.lower().strip("[]").rstrip(".")
            if host in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("SMTP-Ziel darf nicht der lokale Proxy sein.")
            if resolve:
                import ipaddress

                addresses = socket.getaddrinfo(host, self.upstream_port)
                if any(ipaddress.ip_address(item[4][0]).is_loopback for item in addresses):
                    raise ValueError("SMTP-Ziel zeigt auf den lokalen Proxy.")


def validate_address(value: str) -> str:
    if any(char in value for char in "\r\n\x00") or not value.isascii():
        raise ValueError("Eine einzelne ASCII-E-Mail-Adresse ist erforderlich.")
    try:
        address = Address(addr_spec=value)
        if not address.username or not address.domain or address.addr_spec != value:
            raise ValueError
    except (ValueError, IndexError) as error:
        raise ValueError("Ungueltige E-Mail-Adresse.") from error
    return value


def load_settings(path: Path | None = None) -> Settings:
    path = path or data_dir() / ".env"
    if not path.exists():
        return Settings()
    values = dotenv_values(path, interpolate=False, encoding="utf-8")
    arguments = {}
    defaults = Settings()
    try:
        for item in fields(Settings):
            if item.name == "password":
                encoded = values.get("UPSTREAM_PASSWORD_B64", "") or ""
                arguments[item.name] = base64.b64decode(encoded, validate=True).decode("utf-8")
            elif item.name.upper() in values:
                value = values[item.name.upper()]
                default = getattr(defaults, item.name)
                if isinstance(default, bool):
                    if value not in {"true", "false"}:
                        raise ValueError("Autostart muss true oder false sein.")
                    arguments[item.name] = value == "true"
                elif isinstance(default, int):
                    arguments[item.name] = int(value or "")
                else:
                    arguments[item.name] = value or ""
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise ValueError("Ungueltige .env: Zahlen, Wahrheitswerte oder Base64-Kennwort pruefen.") from error
    return Settings(**arguments)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    settings.validate()
    path = path or data_dir() / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    output = io.StringIO()
    for item in fields(settings):
        key = item.name.upper()
        value = getattr(settings, item.name)
        if item.name == "password":
            key = "UPSTREAM_PASSWORD_B64"
            value = base64.b64encode(value.encode("utf-8")).decode("ascii")
        elif isinstance(value, bool):
            value = str(value).lower()
        quoted = str(value).replace("\\", "\\\\").replace("'", "\\'")
        output.write(f"{key}='{quoted}'\n")
    descriptor, temporary = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(output.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
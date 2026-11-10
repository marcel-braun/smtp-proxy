import smtplib
import socket
import ssl
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser

from smtp_proxy.config import Settings


def prepare_message(sender: str, content: bytes, force_from: str):
    if not force_from or not sender:
        return sender, content
    message = BytesParser(policy=policy.SMTP).parsebytes(content)
    while "From" in message:
        del message["From"]
    message["From"] = force_from
    return force_from, message.as_bytes()


class UpstreamClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def connect(self):
        settings = self.settings
        settings.validate(resolve=True)
        context = ssl.create_default_context()
        if settings.upstream_accept_invalid_certificate:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if settings.upstream_security == "tls":
            client = smtplib.SMTP_SSL(
                settings.upstream_host, settings.upstream_port,
                timeout=settings.smtp_timeout_seconds, context=context,
            )
        else:
            client = smtplib.SMTP(
                settings.upstream_host, settings.upstream_port,
                timeout=settings.smtp_timeout_seconds,
            )
        try:
            client.ehlo_or_helo_if_needed()
            if settings.upstream_security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            client.login(settings.upstream_username, settings.password)
            yield client
        finally:
            client.close()

    def test_connection(self):
        with self.connect() as client:
            code, _ = client.noop()
            if code != 250:
                raise smtplib.SMTPResponseException(code, b"NOOP failed")

    def send(self, sender, recipients, content, options=()):
        with self.connect() as client:
            if any(byte > 127 for byte in content) and not client.has_extn("8bitmime"):
                raise smtplib.SMTPNotSupportedError("Upstream unterstuetzt kein 8BITMIME")
            mail_options = ["BODY=8BITMIME"] if client.has_extn("8bitmime") else []
            try:
                return client.sendmail(sender, recipients, content, mail_options=mail_options)
            except smtplib.SMTPRecipientsRefused as error:
                return error.recipients


def safe_error(error: Exception) -> str:
    if isinstance(error, ssl.SSLCertVerificationError):
        detail = error.verify_message or "Zertifikatspruefung fehlgeschlagen"
        return f"TLS-Zertifikat abgelehnt: {detail} (Code {error.verify_code})"
    if isinstance(error, ssl.SSLError):
        detail = error.strerror or "TLS-Verhandlung fehlgeschlagen"
        return f"TLS-Fehler: {detail}"
    if isinstance(error, socket.gaierror):
        detail = error.strerror or "Zielservernamen und DNS-Einstellungen pruefen"
        return f"DNS-Aufloesung des Zielservers fehlgeschlagen: {detail} (Code {error.errno})"
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return f"SMTP-Anmeldung abgelehnt (SMTP {error.smtp_code}); Benutzername und Kennwort pruefen."
    if isinstance(error, smtplib.SMTPResponseException):
        return f"{type(error).__name__} (SMTP {error.smtp_code})"
    if isinstance(error, PermissionError):
        if str(error) == "Administrator rights are required.":
            return "Administratorrechte erforderlich; GUI als Administrator starten."
        detail = error.strerror or "Zugriff verweigert"
        code = f" (Windows-Fehler {error.winerror})" if error.winerror else ""
        return f"{detail}{code}; Datei- und Ordnerberechtigungen pruefen."
    if isinstance(error, FileNotFoundError):
        return "Datei oder Ordner nicht gefunden; Installations- und Speicherpfad pruefen."
    if isinstance(error, TimeoutError):
        return "Zeitueberschreitung; Server, Port und Netzwerkverbindung pruefen."
    if isinstance(error, OSError):
        detail = error.strerror or "Systemfehler beim Zugriff auf Datei oder Netzwerk"
        code = f" (Windows-Fehler {error.winerror})" if error.winerror else (
            f" (Fehler {error.errno})" if error.errno else "")
        return f"{detail}{code}"
    return type(error).__name__
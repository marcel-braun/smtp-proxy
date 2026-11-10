import smtplib
import socket
import ssl
from contextlib import contextmanager
from unittest.mock import patch

import pytest
import trustme
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from smtp_proxy.relay import UpstreamClient, safe_error
from test_config import configured
from test_smtp_server import free_port


class Destination:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append((envelope.mail_from, envelope.rcpt_tos, envelope.original_content))
        return "250 OK"


def authenticate(server, session, envelope, mechanism, auth_data):
    success = (isinstance(auth_data, LoginPassword)
               and auth_data.login == b"user" and auth_data.password == b"secret")
    return AuthResult(success=success, handled=False)


@contextmanager
def destination(mode, tmp_path):
    authority = trustme.CA()
    certificate = authority.issue_cert("localhost")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(server_context)
    client_context = ssl.create_default_context()
    authority.configure_trust(client_context)
    handler = Destination()
    arguments = {"authenticator": authenticate, "auth_required": True,
                 "auth_require_tls": mode == "starttls", "enable_SMTPUTF8": False}
    if mode == "starttls":
        arguments["tls_context"] = server_context
        arguments["require_starttls"] = True
    elif mode == "tls":
        arguments["ssl_context"] = server_context
    port = free_port()
    controller = Controller(handler, hostname="127.0.0.1", port=port, **arguments)
    controller.start()
    try:
        yield handler, port, client_context
    finally:
        controller.stop()


@pytest.mark.parametrize("mode", ["none", "starttls", "tls"])
def test_authenticated_delivery(mode, tmp_path):
    with destination(mode, tmp_path) as (handler, port, context):
        settings = configured(upstream_host="localhost", upstream_port=port, upstream_security=mode)
        with patch("smtp_proxy.relay.ssl.create_default_context", return_value=context):
            client = UpstreamClient(settings)
            client.test_connection()
            content = b"From: header@example.com\r\n\r\n.Body\r\n"
            assert client.send("envelope@example.com", ["recipient@example.com"], content) == {}
        assert handler.messages == [("envelope@example.com", ["recipient@example.com"], content)]


def test_bad_password_rejected(tmp_path):
    with destination("starttls", tmp_path) as (handler, port, context):
        settings = configured(upstream_host="localhost", upstream_port=port, password="wrong")
        with patch("smtp_proxy.relay.ssl.create_default_context", return_value=context):
            with pytest.raises(smtplib.SMTPAuthenticationError):
                UpstreamClient(settings).test_connection()
        assert handler.messages == []


def test_untrusted_certificate_rejected(tmp_path):
    with destination("tls", tmp_path) as (handler, port, context):
        settings = configured(upstream_host="localhost", upstream_port=port, upstream_security="tls")
        with pytest.raises(ssl.SSLCertVerificationError) as error:
            UpstreamClient(settings).test_connection()
        assert "TLS-Zertifikat abgelehnt" in safe_error(error.value)
        assert handler.messages == []


@pytest.mark.parametrize("mode", ["starttls", "tls"])
def test_untrusted_certificate_can_be_accepted_when_explicitly_configured(mode, tmp_path):
    with destination(mode, tmp_path) as (handler, port, context):
        settings = configured(
            upstream_host="127.0.0.1", upstream_port=port, upstream_security=mode,
            upstream_accept_invalid_certificate=True,
        )
        UpstreamClient(settings).test_connection()
        assert handler.messages == []


def test_starttls_never_falls_back(tmp_path):
    with destination("none", tmp_path) as (handler, port, context):
        settings = configured(upstream_host="localhost", upstream_port=port)
        with pytest.raises(smtplib.SMTPNotSupportedError):
            UpstreamClient(settings).test_connection()
        assert handler.messages == []


def test_dns_errors_are_actionable_and_redacted():
    message = safe_error(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
    assert "DNS-Aufloesung" in message
    assert "Name or service not known" in message
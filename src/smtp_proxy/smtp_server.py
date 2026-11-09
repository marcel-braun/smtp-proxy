import asyncio
import errno
import logging
import sqlite3
import threading

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP

from smtp_proxy.queue_store import QueueFullError
from smtp_proxy.relay import prepare_message


class LocalSMTPHandler:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings

    async def handle_EHLO(self, server, session, envelope, hostname, responses):
        session.host_name = hostname
        return [line for line in responses if not line.startswith("250-AUTH")]

    async def handle_AUTH(self, server, session, envelope, args):
        return "502 Authentication is not supported"

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if len(envelope.rcpt_tos) >= 100:
            return "452 Too many recipients"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        try:
            content = envelope.original_content
            if len(content) > self.settings.max_message_bytes:
                return "552 Message too large"
            identifier = await asyncio.to_thread(
                self.persist, envelope.mail_from, envelope.rcpt_tos, content, envelope.mail_options,
            )
            return f"250 Queued as {identifier}"
        except QueueFullError:
            return "452 Insufficient queue capacity"
        except (OSError, sqlite3.Error, ValueError):
            return "451 Could not persist message"

    def persist(self, sender, recipients, content, options):
        sender, content = prepare_message(sender, content, self.settings.force_from)
        return self.store.enqueue(
            sender, recipients, content, options, max_bytes=self.settings.max_queue_bytes,
        )


class LimitedSMTP(SMTP):
    active = 0
    guard = threading.Lock()

    def connection_made(self, transport):
        self.counted = True
        with self.guard:
            type(self).active += 1
            excess = type(self).active > 20
        super().connection_made(transport)
        if excess:
            transport.close()

    def connection_lost(self, error):
        if getattr(self, "counted", False):
            with self.guard:
                type(self).active -= 1
            self.counted = False
        super().connection_lost(error)


class LocalController(Controller):
    def factory(self):
        return LimitedSMTP(self.handler, **self.SMTP_kwargs)


class LocalServer:
    def __init__(self, store, settings):
        self.handler = LocalSMTPHandler(store, settings)
        self.settings = settings
        self.controllers = []

    def start(self):
        logging.getLogger("mail.log").disabled = True
        try:
            for host in ("127.0.0.1", "::1"):
                controller = LocalController(
                    self.handler, hostname=host, port=self.settings.local_port,
                    enable_SMTPUTF8=False, decode_data=False,
                    data_size_limit=self.settings.max_message_bytes,
                    auth_require_tls=True, auth_required=False,
                    ready_timeout=5,
                )
                try:
                    controller.start()
                except OSError as error:
                    controller.stop(no_assert=True)
                    if host == "::1" and (error.errno in {errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL}
                                          or getattr(error, "winerror", None) in {10047, 10049}):
                        continue
                    raise
                except Exception:
                    controller.stop(no_assert=True)
                    raise
                self.controllers.append(controller)
        except Exception:
            self.stop()
            raise

    def stop(self):
        for controller in reversed(self.controllers):
            controller.stop()
        self.controllers.clear()
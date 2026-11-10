import logging
import smtplib
import threading
import time

from smtp_proxy.relay import UpstreamClient, safe_error

logger = logging.getLogger("smtp_proxy")


class QueueWorker:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.client = UpstreamClient(settings)
        self.stopping = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        self.thread = threading.Thread(target=self.run, name="smtp-queue", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopping.is_set():
            try:
                if self.process_one():
                    continue
            except Exception as error:
                logger.error("Queue-Worker: %s", safe_error(error))
            self.stopping.wait(1)

    def process_one(self):
        with self.lock:
            job = self.store.next_due(self.settings.max_queue_age_hours)
            if job is None:
                return False
            recipients = job["recipients"]
            try:
                refused = self.client.send(
                    job["sender"], [item["address"] for item in recipients],
                    job["content"], job["options"],
                )
                outcomes = {}
                for recipient in recipients:
                    address = recipient["address"]
                    if address not in refused:
                        outcomes[address] = ("sent", 0, "")
                    else:
                        code, _ = refused[address]
                        outcomes[address] = self.failure(recipient, code, f"SMTP {code}")
            except Exception as error:
                permanent = isinstance(error, (smtplib.SMTPDataError, smtplib.SMTPSenderRefused))
                code = error.smtp_code if permanent else 0
                outcomes = {
                    item["address"]: self.failure(item, code, safe_error(error))
                    for item in recipients
                }
            self.store.record(job["id"], outcomes)
            logger.info("Queue %s verarbeitet: %s", job["id"],
                        ", ".join(sorted({item[0] for item in outcomes.values()})))
            return True

    def failure(self, recipient, code, message):
        if 500 <= code < 600:
            return "failed", 0, message
        delay = min(
            self.settings.retry_max_seconds,
            self.settings.retry_initial_seconds * 2 ** min(recipient["attempts"], 16),
        )
        return "pending", time.time() + delay, message

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(self.settings.smtp_timeout_seconds * 5 + 5)
            if self.thread.is_alive():
                raise TimeoutError("SMTP-Worker wurde nicht rechtzeitig beendet.")
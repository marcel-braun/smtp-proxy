import logging
import msvcrt
from logging.handlers import RotatingFileHandler

from smtp_proxy.queue_store import QueueStore
from smtp_proxy.smtp_server import LocalServer
from smtp_proxy.worker import QueueWorker


class ProxyRuntime:
    def __init__(self, settings, directory):
        self.settings = settings
        self.directory = directory
        self.lock_file = None
        self.server = None
        self.worker = None
        self.log_handler = None

    def start(self):
        self.settings.validate()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.directory / "runtime.lock").open("a+b")
        self.lock_file.seek(0)
        try:
            msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("Eine Proxy-Instanz verwendet diese Queue bereits.") from None
        try:
            logs = self.directory / "logs"
            logs.mkdir(exist_ok=True)
            self.log_handler = RotatingFileHandler(
                logs / "proxy.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
            )
            self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger = logging.getLogger("smtp_proxy")
            logger.setLevel(logging.INFO)
            logger.addHandler(self.log_handler)
            store = QueueStore(self.directory / "queue.sqlite3")
            self.server = LocalServer(store, self.settings)
            self.server.start()
            self.worker = QueueWorker(store, self.settings)
            self.worker.start()
            logger.info("Lokaler SMTP-Empfang gestartet, Port %d", self.settings.local_port)
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.server:
            self.server.stop()
            self.server = None
        if self.worker:
            self.worker.stop()
            self.worker = None
        if self.log_handler:
            logging.getLogger("smtp_proxy").removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = None
        if self.lock_file:
            self.lock_file.seek(0)
            msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            self.lock_file.close()
            self.lock_file = None
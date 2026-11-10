import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler

import servicemanager
import win32service
import win32serviceutil

from smtp_proxy.config import load_settings
from smtp_proxy.paths import application_dir, service_settings_path
from smtp_proxy.service_manager import SERVICE_NAME, verify_data_dir, verify_settings_file

OPERATION_TIMEOUT = 130


class SMTPProxyService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "SMTPProxy"
    _svc_description_ = "Local loopback SMTP relay"

    def __init__(self, args):
        super().__init__(args)
        self._stop_event = threading.Event()
        self._status_lock = threading.RLock()
        self._state = win32service.SERVICE_START_PENDING
        self._logger = logging.getLogger("smtp_proxy.service")

    def _report(self, state, **kwargs):
        with self._status_lock:
            self._state = state
            self.ReportServiceStatus(state, **kwargs)

    def SvcInterrogate(self):
        with self._status_lock:
            self.ReportServiceStatus(self._state)

    def SvcStop(self):
        with self._status_lock:
            self._stop_event.set()
            self._report(win32service.SERVICE_STOP_PENDING, waitHint=10000)

    def SvcShutdown(self):
        self.SvcStop()

    def _operation(self, operation, pending):
        completed = threading.Event()
        errors = []

        def execute():
            try:
                operation()
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()

        threading.Thread(target=execute, daemon=True, name="service-lifecycle").start()
        deadline = time.monotonic() + OPERATION_TIMEOUT
        while not completed.wait(1):
            state = (win32service.SERVICE_STOP_PENDING if self._stop_event.is_set() else pending)
            self._report(state, waitHint=10000)
            if time.monotonic() >= deadline:
                self._logger.error("Lifecycle operation timed out; terminating for SCM recovery.")
                servicemanager.LogErrorMsg("SMTPProxy lifecycle operation timed out.")
                os._exit(1)
        if errors:
            raise errors[0]

    def _configure_log(self, directory):
        logs = directory / "logs"
        logs.mkdir(exist_ok=True)
        handler = RotatingFileHandler(logs / "service.log", maxBytes=5 * 1024 * 1024,
                                      backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._logger.handlers.clear()
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

    def SvcRun(self):
        runtime = None
        failed = False
        self._report(win32service.SERVICE_START_PENDING, waitHint=10000)
        try:
            directory = verify_data_dir()
            self._configure_log(directory)
            settings_path = service_settings_path(directory)
            if settings_path.parent == application_dir() and settings_path.is_file():
                verify_settings_file(settings_path)
            settings = load_settings(settings_path)
            settings.validate()
            from smtp_proxy.runtime import ProxyRuntime

            runtime = ProxyRuntime(settings, directory)
            self._operation(runtime.start, win32service.SERVICE_START_PENDING)
            with self._status_lock:
                if not self._stop_event.is_set():
                    self._report(win32service.SERVICE_RUNNING)
            self._logger.info("Service runtime started.")
            self._stop_event.wait()
        except BaseException as error:
            failed = True
            error_type = type(error).__name__
            self._logger.error("Service startup/run failed (%s).", error_type)
            servicemanager.LogErrorMsg(f"SMTPProxy startup/run failed ({error_type}).")
        finally:
            self._report(win32service.SERVICE_STOP_PENDING, waitHint=10000)
            if runtime is not None:
                try:
                    self._operation(runtime.stop, win32service.SERVICE_STOP_PENDING)
                except BaseException as error:
                    failed = True
                    self._logger.error("Runtime stop failed (%s).", type(error).__name__)
                    servicemanager.LogErrorMsg("SMTPProxy runtime stop failed.")
                    os._exit(1)
            self._report(win32service.SERVICE_STOPPED,
                         win32ExitCode=1066 if failed else 0, svcExitCode=1 if failed else 0)
            for handler in self._logger.handlers[:]:
                handler.close()
                self._logger.removeHandler(handler)
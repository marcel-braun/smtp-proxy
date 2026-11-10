import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from smtp_proxy.config import load_settings
from smtp_proxy.paths import default_portable_dir, service_settings_path
from smtp_proxy.relay import safe_error
from smtp_proxy.runtime import ProxyRuntime


def verify_data_dir():
    if sys.platform != "win32":
        raise RuntimeError("Der Windows-Dienstmodus ist auf Linux nicht verfuegbar.")
    from smtp_proxy.service_manager import verify_data_dir as verify_windows_data_dir

    return verify_windows_data_dir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SMTPProxy als Konsolenprozess ohne Windows-Dienst ausfuehren.",
    )
    location = parser.add_mutually_exclusive_group()
    location.add_argument(
        "--data-dir", type=Path, metavar="VERZEICHNIS",
        help="Verzeichnis mit .env, Queue und Logs; portabel standardmaessig neben der EXE.",
    )
    location.add_argument("--service-mode", action="store_true",
                          help="App-Konfiguration neben der EXE, Laufzeitdaten in ProgramData verwenden.")
    parser.add_argument(
        "--check", action="store_true",
        help="Konfiguration pruefen und beenden, ohne Listener oder Worker zu starten.",
    )
    options = parser.parse_args(argv)
    if options.service_mode and sys.platform != "win32":
        parser.error("--service-mode ist nur unter Windows verfuegbar.")
    runtime = None
    handlers = {}
    console = None
    result = 0
    logger = logging.getLogger("smtp_proxy")
    previous_level = logger.level
    stopping = threading.Event()

    def request_stop(signum, frame):
        stopping.set()

    try:
        directory = options.data_dir
        portable = directory is not None
        if directory is None and not options.service_mode:
            directory = default_portable_dir()
            portable = directory is not None
        directory = directory.expanduser().resolve() if directory is not None else verify_data_dir()
        path = directory / ".env" if portable else service_settings_path(directory)
        if not path.is_file():
            print(f"Keine Konfiguration gefunden: {path}", file=sys.stderr)
            return 1
        if not portable and path.parent != directory:
            from smtp_proxy.service_manager import verify_settings_file

            verify_settings_file(path)
        settings = load_settings(path)
        settings.validate()
        if options.check:
            print(f"Konfiguration gueltig: {path}")
            return 0
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            signum = getattr(signal, name, None)
            if signum is not None:
                handlers[signum] = signal.signal(signum, request_stop)
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(console)
        runtime = ProxyRuntime(settings, directory)
        runtime.start()
        print(f"SMTPProxy laeuft auf localhost:{settings.local_port}. Beenden mit Strg+C.", flush=True)
        stopping.wait()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f"SMTPProxy konnte nicht ausgefuehrt werden: {safe_error(error)}", file=sys.stderr)
        result = 1
    finally:
        if runtime is not None:
            try:
                runtime.stop()
            except Exception as error:
                print(f"Fehler beim Beenden: {safe_error(error)}", file=sys.stderr)
                result = 1
        for signum, previous in handlers.items():
            signal.signal(signum, previous)
        if console is not None:
            logger.removeHandler(console)
            console.close()
        logger.setLevel(previous_level)
    return result
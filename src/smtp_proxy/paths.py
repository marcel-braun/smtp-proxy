import os
import sys
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "SMTPProxy"


def application_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[2]
    return Path(sys.executable).resolve().parent


def service_settings_path(data_directory: Path) -> Path:
    app_settings = application_dir() / ".env"
    legacy_settings = Path(data_directory) / ".env"
    if app_settings.is_file() or not legacy_settings.is_file():
        return app_settings
    return legacy_settings


def default_portable_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return application_dir()
    from win32com.shell import shell, shellcon

    directory = application_dir()
    installed = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAM_FILES, None, 0)) / "SMTPProxy"
    return None if directory == installed.resolve() else directory
import sys

import pytest

from smtp_proxy import paths


def test_source_portable_directory_is_project_root(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    before = paths.default_portable_dir()
    monkeypatch.chdir(tmp_path)
    assert paths.default_portable_dir() == before
    assert (before / "app_gui.py").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders require Windows")
def test_frozen_portable_directory(monkeypatch, tmp_path):
    from win32com.shell import shell

    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "portable" / "app_gui.exe"))
    monkeypatch.setattr(shell, "SHGetFolderPath", lambda *args: str(tmp_path / "Program Files"))
    assert paths.default_portable_dir() == (tmp_path / "portable").resolve()


def test_frozen_linux_directory_is_portable(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.sys, "executable", str(tmp_path / "portable" / "app_cli"))
    assert paths.default_portable_dir() == (tmp_path / "portable").resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders require Windows")
def test_frozen_installed_directory_uses_programdata(monkeypatch, tmp_path):
    from win32com.shell import shell

    root = tmp_path / "Program Files"
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(root / "SMTPProxy" / "app_gui.exe"))
    monkeypatch.setattr(shell, "SHGetFolderPath", lambda *args: str(root))
    assert paths.default_portable_dir() is None


def test_service_settings_prefers_app_folder_and_falls_back_to_programdata(
    monkeypatch, tmp_path,
):
    app_dir = tmp_path / "app"
    data_dir = tmp_path / "ProgramData" / "SMTPProxy"
    app_dir.mkdir()
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(paths, "application_dir", lambda: app_dir)

    assert paths.service_settings_path(data_dir) == app_dir / ".env"
    (data_dir / ".env").touch()
    assert paths.service_settings_path(data_dir) == data_dir / ".env"
    (app_dir / ".env").touch()
    assert paths.service_settings_path(data_dir) == app_dir / ".env"
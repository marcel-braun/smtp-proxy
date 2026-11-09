from unittest.mock import MagicMock
from contextlib import contextmanager
import importlib.util
import runpy
import sys
import threading
import types
from pathlib import Path

import pytest
import pywintypes
import ntsecuritycon
import win32service
import win32security
import win32con

from smtp_proxy import service_manager as manager


@pytest.mark.parametrize("name,args", [
    ("install", ()), ("start", ()), ("stop", ()), ("restart", ()),
    ("remove", ()), ("set_autostart", (True,)), ("initialize_data_dir", ()),
])
def test_writes_require_admin(monkeypatch, name, args):
    monkeypatch.setattr(manager, "is_admin", lambda: False)
    scm = MagicMock()
    monkeypatch.setattr(win32service, "OpenSCManager", scm)
    with pytest.raises(PermissionError):
        getattr(manager, name)(*args)
    scm.assert_not_called()


def test_source_install_refused(monkeypatch):
    monkeypatch.setattr(manager, "is_admin", lambda: True)
    monkeypatch.delattr(manager.sys, "frozen", raising=False)
    with pytest.raises(RuntimeError, match="frozen onedir"):
        manager.install()


def test_status_read_does_not_require_admin(monkeypatch):
    monkeypatch.setattr(manager, "is_admin", lambda: False)
    monkeypatch.setattr(win32service, "OpenSCManager", lambda *args: "scm")
    monkeypatch.setattr(win32service, "OpenService", lambda *args: "service")
    close = MagicMock()
    monkeypatch.setattr(win32service, "CloseServiceHandle", close)
    monkeypatch.setattr(win32service, "QueryServiceStatus", lambda *args: (16, 4, 0, 0, 0, 0, 0))
    assert manager.status() == "running"
    assert close.call_count == 2


def test_missing_service(monkeypatch):
    monkeypatch.setattr(win32service, "OpenSCManager", lambda *args: "scm")
    monkeypatch.setattr(win32service, "CloseServiceHandle", lambda *args: None)
    def missing(*args):
        raise pywintypes.error(1060, "OpenService", "missing")
    monkeypatch.setattr(win32service, "OpenService", missing)
    assert manager.status() == "not_installed"
    assert not manager.installed()
    assert not manager.autostart_enabled()


def test_wait_is_bounded(monkeypatch):
    monkeypatch.setattr(win32service, "QueryServiceStatus", lambda *args: (16, 2, 0, 0, 0, 0, 0))
    ticks = iter([0, manager.WAIT_SECONDS + 1])
    monkeypatch.setattr(manager.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError):
        manager._wait("service", win32service.SERVICE_RUNNING)


def test_start_failure_is_not_success(monkeypatch):
    monkeypatch.setattr(win32service, "QueryServiceStatus", lambda *args: (16, 1, 0, 1066, 1, 0, 0))
    with pytest.raises(RuntimeError, match="startup failed"):
        manager._wait("service", win32service.SERVICE_RUNNING)


@pytest.fixture
def cli():
    path = Path(__file__).parents[1] / "app_service.py"
    spec = importlib.util.spec_from_file_location("service_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("command", ["install", "start", "stop", "restart", "remove"])
def test_cli_dispatch_and_failure(monkeypatch, cli, command):
    operation = MagicMock()
    monkeypatch.setattr(manager, command, operation)
    assert cli.main([command]) == 0
    operation.assert_called_once_with()
    operation.side_effect = PermissionError("secret password must not be printed")
    assert cli.main([command]) == 1


@pytest.mark.parametrize("argument,expected", [("--auto", True), ("--manual", False)])
def test_cli_startup(monkeypatch, cli, argument, expected):
    operation = MagicMock()
    monkeypatch.setattr(manager, "set_autostart", operation)
    assert cli.main(["set-startup", argument]) == 0
    operation.assert_called_once_with(expected)


def test_cli_no_source_scm(monkeypatch, cli):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert cli.main([]) == 1


def test_cli_explains_install_path_refusal_without_leaking_errors(monkeypatch, cli, capsys):
    operation = MagicMock(side_effect=manager.ServiceManagerError(
        "Install the frozen onedir application in Program Files first."))
    monkeypatch.setattr(manager, "install", operation)
    assert cli.main(["install"]) == 1
    assert "frozen onedir" in capsys.readouterr().err
    operation.side_effect = ValueError("secret password and message body")
    assert cli.main(["install"]) == 1
    assert "secret" not in capsys.readouterr().err


@pytest.fixture
def service(monkeypatch):
    from smtp_proxy import windows_service as host
    instance = object.__new__(host.SMTPProxyService)
    instance._stop_event = threading.Event()
    instance._status_lock = threading.RLock()
    instance._state = win32service.SERVICE_START_PENDING
    instance._logger = MagicMock()
    instance._logger.handlers = []
    instance.ReportServiceStatus = MagicMock()
    instance._configure_log = MagicMock()
    monkeypatch.setattr(host, "verify_data_dir", lambda: Path("C:/protected"))
    settings = MagicMock()
    monkeypatch.setattr(host, "load_settings", lambda *args: settings)
    monkeypatch.setattr(host.servicemanager, "LogErrorMsg", MagicMock())
    return instance


def test_running_only_after_runtime_start(monkeypatch, service):
    runtime = MagicMock()
    def started():
        states = [call.args[0] for call in service.ReportServiceStatus.call_args_list]
        assert win32service.SERVICE_RUNNING not in states
    runtime.start.side_effect = started
    runtime.stop.side_effect = lambda: None
    factory = MagicMock(return_value=runtime)
    monkeypatch.setitem(sys.modules, "smtp_proxy.runtime", types.SimpleNamespace(ProxyRuntime=factory))
    service._stop_event.wait = lambda *args: service.SvcShutdown()
    service.SvcRun()
    states = [call.args[0] for call in service.ReportServiceStatus.call_args_list]
    assert win32service.SERVICE_RUNNING in states
    runtime.stop.assert_called_once()
    assert states[-1] == win32service.SERVICE_STOPPED
    assert service.ReportServiceStatus.call_args.kwargs["win32ExitCode"] == 0


def test_failed_start_never_reports_running(monkeypatch, service):
    runtime = MagicMock()
    runtime.start.side_effect = ValueError("password and message body")
    monkeypatch.setitem(sys.modules, "smtp_proxy.runtime",
                        types.SimpleNamespace(ProxyRuntime=lambda *args: runtime))
    service.SvcRun()
    states = [call.args[0] for call in service.ReportServiceStatus.call_args_list]
    assert win32service.SERVICE_RUNNING not in states
    runtime.stop.assert_called_once()
    assert service.ReportServiceStatus.call_args.kwargs["win32ExitCode"] == 1066
    assert "password" not in str(service._logger.mock_calls)


def test_data_acl_only_allows_system_admins_localservice():
    acl = manager._acl()
    entries = {win32security.ConvertSidToStringSid(acl.GetAce(index)[2]): acl.GetAce(index)
               for index in range(acl.GetAceCount())}
    assert set(entries) == {"S-1-5-18", "S-1-5-32-544", "S-1-5-19"}
    assert entries["S-1-5-18"][1] == ntsecuritycon.FILE_ALL_ACCESS
    local_access = entries["S-1-5-19"][1]
    assert local_access & ntsecuritycon.FILE_WRITE_DATA
    assert not local_access & (win32con.WRITE_DAC | win32con.WRITE_OWNER)
    assert entries["S-1-5-19"][0][1] == (
        win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE)


def test_existing_data_contents_are_secured(monkeypatch, tmp_path):
    root = tmp_path / "SMTPProxy"
    root.mkdir()
    (root / ".env").touch()
    (root / "queue.sqlite3").touch()
    (root / "logs").mkdir()
    (root / "logs" / "old.log").touch()
    monkeypatch.setattr(manager, "is_admin", lambda: True)
    monkeypatch.setattr(manager, "_trusted_root", lambda folder: tmp_path)
    monkeypatch.setattr(manager, "data_dir", lambda: root)
    def exists(*args):
        raise pywintypes.error(183, "CreateDirectory", "exists")
    monkeypatch.setattr(manager.win32file, "CreateDirectory", exists)
    setter = MagicMock()
    monkeypatch.setattr(win32security, "SetNamedSecurityInfo", setter)
    assert manager.initialize_data_dir() == root
    secured = {Path(call.args[0]).relative_to(root).as_posix() for call in setter.call_args_list}
    assert secured == {".", ".env", "queue.sqlite3", "logs", "logs/old.log"}
    for call in setter.call_args_list:
        assert call.args[2] & win32security.PROTECTED_DACL_SECURITY_INFORMATION


def test_service_rejects_all_users_data_acl(monkeypatch, tmp_path):
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
                            win32security.ConvertStringSidToSid("S-1-5-32-545"))
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(win32security.ConvertStringSidToSid("S-1-5-32-544"), False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    monkeypatch.setattr(win32security, "GetNamedSecurityInfo", lambda *args: descriptor)
    with pytest.raises(PermissionError, match="untrusted account"):
        manager._verify_tree(tmp_path)


@pytest.fixture
def fake_scm(monkeypatch):
    monkeypatch.setattr(manager, "is_admin", lambda: True)
    @contextmanager
    def opened(access):
        yield "service"
    monkeypatch.setattr(manager, "_service", opened)
    for name in ("OpenSCManager", "CreateService", "CloseServiceHandle", "DeleteService",
                 "ChangeServiceConfig2", "ChangeServiceConfig", "StartService", "ControlService"):
        monkeypatch.setattr(win32service, name, MagicMock(return_value="handle"))
    return win32service


def test_install_rolls_back_if_recovery_configuration_fails(monkeypatch, fake_scm):
    monkeypatch.setattr(manager, "_installed_executable", lambda name: Path("C:/Program Files/SMTPProxy") / name)
    monkeypatch.setattr(manager, "_protect_tree", MagicMock())
    monkeypatch.setattr(manager, "initialize_data_dir", MagicMock())
    monkeypatch.setattr(manager, "installed", lambda: False)
    fake_scm.ChangeServiceConfig2.side_effect = OSError("failed")
    with pytest.raises(OSError):
        manager.install()
    fake_scm.DeleteService.assert_called_once_with("handle")
    create = fake_scm.CreateService.call_args.args
    assert create[5] == win32service.SERVICE_DEMAND_START
    assert create[7] == '"C:\\Program Files\\SMTPProxy\\app_service.exe"'
    assert create[11] == "NT AUTHORITY\\LocalService"


@pytest.mark.parametrize("enabled", [True, False])
def test_autostart_is_delayed_auto_or_manual(fake_scm, enabled):
    manager.set_autostart(enabled)
    assert fake_scm.ChangeServiceConfig.call_args.args[2] == (
        win32service.SERVICE_AUTO_START if enabled else win32service.SERVICE_DEMAND_START)
    fake_scm.ChangeServiceConfig2.assert_called_once_with(
        "service", win32service.SERVICE_CONFIG_DELAYED_AUTO_START_INFO, enabled)


def test_start_is_idempotent_when_running(monkeypatch, fake_scm):
    monkeypatch.setattr(win32service, "QueryServiceStatus", lambda *args: (16, 4, 0, 0, 0, 0, 0))
    manager.start()
    fake_scm.StartService.assert_not_called()


def test_stop_is_idempotent_when_stopped(monkeypatch, fake_scm):
    monkeypatch.setattr(win32service, "QueryServiceStatus", lambda *args: (16, 1, 0, 0, 0, 0, 0))
    manager.stop()
    fake_scm.ControlService.assert_not_called()


def test_remove_waits_until_deleted(monkeypatch, fake_scm):
    monkeypatch.setattr(manager, "stop", MagicMock())
    states = iter([True, False])
    monkeypatch.setattr(manager, "installed", lambda: next(states))
    monkeypatch.setattr(manager.time, "sleep", MagicMock())
    manager.remove()
    fake_scm.DeleteService.assert_called_once_with("service")


def test_elevation_requires_safe_installed_binary(monkeypatch):
    monkeypatch.setattr(manager, "_installed_executable", lambda name: Path("C:/Program Files/SMTPProxy") / name)
    verifier = MagicMock(side_effect=PermissionError("unsafe"))
    monkeypatch.setattr(manager, "_verify_tree", verifier)
    execute = MagicMock()
    monkeypatch.setattr(manager.win32api, "ShellExecute", execute)
    with pytest.raises(PermissionError):
        manager.elevate_gui()
    execute.assert_not_called()


def test_elevation_never_passes_credentials_or_cli_args(monkeypatch):
    path = Path("C:/Program Files/SMTPProxy/app_gui.exe")
    monkeypatch.setattr(manager, "_installed_executable", lambda name: path)
    monkeypatch.setattr(manager, "_verify_tree", MagicMock())
    execute = MagicMock(return_value=33)
    monkeypatch.setattr(manager.win32api, "ShellExecute", execute)
    manager.elevate_gui()
    execute.assert_called_once_with(0, "runas", str(path), None, str(path.parent), 1)


def test_network_install_refused(monkeypatch, tmp_path):
    root = tmp_path / "SMTPProxy"
    root.mkdir()
    (root / "app_service.exe").touch()
    (root / "_internal").mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / "app_gui.exe"))
    monkeypatch.setattr(manager, "_trusted_root", lambda folder: tmp_path)
    monkeypatch.setattr(manager.win32file, "GetDriveType", lambda drive: win32con.DRIVE_REMOTE)
    with pytest.raises(RuntimeError, match="network drive"):
        manager._installed_executable("app_service.exe")


def test_scm_dispatch_only_on_no_arguments(monkeypatch, cli):
    import servicemanager
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    initialize = MagicMock()
    host = MagicMock()
    dispatch = MagicMock()
    monkeypatch.setattr(servicemanager, "Initialize", initialize)
    monkeypatch.setattr(servicemanager, "PrepareToHostSingle", host)
    monkeypatch.setattr(servicemanager, "StartServiceCtrlDispatcher", dispatch)
    assert cli.main([]) == 0
    initialize.assert_called_once()
    host.assert_called_once()
    dispatch.assert_called_once()


def test_stop_succeeds_when_pending_start_fails(monkeypatch, fake_scm):
    states = iter([2, 1, 1])
    monkeypatch.setattr(win32service, "QueryServiceStatus",
                        lambda *args: (16, next(states), 0, 1066, 1, 0, 0))
    manager.stop()
    fake_scm.ControlService.assert_not_called()


def test_lifecycle_timeout_terminates_for_scm_recovery(monkeypatch, service):
    from smtp_proxy import windows_service as host
    completed = MagicMock()
    completed.wait.return_value = False
    monkeypatch.setattr(host.threading, "Event", lambda: completed)
    monkeypatch.setattr(host.threading, "Thread", MagicMock())
    ticks = iter([0, host.OPERATION_TIMEOUT + 1])
    monkeypatch.setattr(host.time, "monotonic", lambda: next(ticks))
    terminate = MagicMock(side_effect=SystemExit(1))
    monkeypatch.setattr(host.os, "_exit", terminate)
    with pytest.raises(SystemExit):
        service._operation(MagicMock(), win32service.SERVICE_START_PENDING)
    terminate.assert_called_once_with(1)


def test_spec_builds_three_exes_in_one_onedir(monkeypatch):
    import PyInstaller.building.api as api
    import PyInstaller.building.build_main as build_main
    import PyInstaller.utils.hooks as hooks
    monkeypatch.setattr(hooks, "copy_metadata", lambda *args, **kwargs: [])
    monkeypatch.setattr(hooks, "collect_data_files", lambda *args, **kwargs: [])
    analysis = MagicMock()
    monkeypatch.setattr(build_main, "Analysis", analysis)
    exe = MagicMock()
    collect = MagicMock()
    monkeypatch.setattr(api, "EXE", exe)
    monkeypatch.setattr(api, "PYZ", MagicMock())
    monkeypatch.setattr(api, "COLLECT", collect)
    root = Path(__file__).parents[1]
    runpy.run_path(str(root / "packaging" / "SMTPProxy.spec"),
                   init_globals={"SPECPATH": str(root / "packaging")})
    assert analysis.call_count == 3
    assert [Path(call.args[0][0]).name for call in analysis.call_args_list] == [
        "app_gui.py", "app_service.py", "app_cli.py"]
    assert [call.kwargs["name"] for call in exe.call_args_list] == [
        "app_gui", "app_service", "app_cli"]
    assert all(call.kwargs["exclude_binaries"] for call in exe.call_args_list)
    assert all(not call.kwargs["uac_admin"] for call in exe.call_args_list)
    assert all(call.kwargs["contents_directory"] == "_internal" for call in exe.call_args_list)
    assert exe.call_args_list[0].kwargs["console"] is False
    assert exe.call_args_list[1].kwargs["console"] is True
    assert exe.call_args_list[2].kwargs["console"] is True
    assert analysis.call_args_list[2].kwargs["excludes"] == ["PySide6"]
    assert collect.call_args.kwargs["name"] == "SMTPProxy"
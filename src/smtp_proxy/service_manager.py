import ctypes
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pywintypes
import ntsecuritycon
import win32api
import win32con
import win32file
import win32security
import win32service
from win32com.shell import shell, shellcon

from smtp_proxy.paths import data_dir

SERVICE_NAME = "SMTPProxy"
WAIT_SECONDS = 150
_STATES = {1: "stopped", 2: "start_pending", 3: "stop_pending", 4: "running",
           5: "continue_pending", 6: "pause_pending", 7: "paused"}


class ServiceManagerError(RuntimeError):
    pass


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _require_admin() -> None:
    if not is_admin():
        raise PermissionError("Administrator rights are required.")


@contextmanager
def _service(access):
    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        service = win32service.OpenService(manager, SERVICE_NAME, access)
        try:
            yield service
        finally:
            win32service.CloseServiceHandle(service)
    finally:
        win32service.CloseServiceHandle(manager)


def _missing(error) -> bool:
    return error.winerror == 1060


def status() -> str:
    try:
        with _service(win32service.SERVICE_QUERY_STATUS) as service:
            return _STATES.get(win32service.QueryServiceStatus(service)[1], "unknown")
    except pywintypes.error as error:
        if _missing(error):
            return "not_installed"
        if error.winerror == 1072:
            return "delete_pending"
        raise


def installed() -> bool:
    return status() != "not_installed"


def autostart_enabled() -> bool:
    try:
        with _service(win32service.SERVICE_QUERY_CONFIG) as service:
            return win32service.QueryServiceConfig(service)[1] == win32service.SERVICE_AUTO_START
    except pywintypes.error as error:
        if _missing(error) or error.winerror == 1072:
            return False
        raise


def _trusted_root(folder: int) -> Path:
    return Path(shell.SHGetFolderPath(0, folder, None, 0)).resolve()


def _installed_executable(name: str) -> Path:
    if not getattr(sys, "frozen", False):
        raise ServiceManagerError("Install the frozen onedir application in Program Files first.")
    root = _trusted_root(shellcon.CSIDL_PROGRAM_FILES) / "SMTPProxy"
    executable = root / name
    if Path(sys.executable).resolve().parent != root or not executable.is_file():
        raise ServiceManagerError("Only the installed Program Files onedir application is supported.")
    if win32file.GetDriveType(str(root.anchor)) != win32con.DRIVE_FIXED:
        raise ServiceManagerError("Service binaries must be on a local fixed drive, not a network drive.")
    for path in (root, executable, root / "_internal"):
        if not path.exists() or path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ServiceManagerError("An intact onedir installation without reparse points is required.")
    return executable


def _acl(binary: bool = False, settings: bool = False):
    acl = win32security.ACL()
    inheritance = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE
    for sid_text in ("S-1-5-18", "S-1-5-32-544"):
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION_DS, inheritance, ntsecuritycon.FILE_ALL_ACCESS,
            win32security.ConvertStringSidToSid(sid_text),
        )
    sid = "S-1-5-19" if settings else "S-1-5-32-545" if binary else "S-1-5-19"
    access = ntsecuritycon.FILE_GENERIC_READ
    if binary:
        access |= ntsecuritycon.FILE_GENERIC_EXECUTE
    elif not settings:
        access |= ntsecuritycon.FILE_GENERIC_WRITE | win32con.DELETE
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, inheritance, access,
                             win32security.ConvertStringSidToSid(sid))
    return acl


def _protect_tree(root: Path, *, binary: bool = False, settings: bool = False) -> None:
    acl = _acl(binary, settings=settings)
    owner = win32security.ConvertStringSidToSid("S-1-5-32-544")
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or info.st_nlink > 1:
            raise ServiceManagerError("Refusing reparse points or hard links in protected storage.")
        win32security.SetNamedSecurityInfo(
            str(path), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION
            | win32security.OWNER_SECURITY_INFORMATION,
            owner, None, acl, None,
        )
        if path.is_dir():
            pending.extend(path.iterdir())


def protect_settings_file(path: Path) -> None:
    _require_admin()
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ServiceManagerError("Settings file must be a regular file.")
    _protect_tree(path, settings=True)


def verify_settings_file(path: Path) -> None:
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ServiceManagerError("Settings file must be a regular file.")
    _verify_tree(path)


def initialize_data_dir() -> Path:
    _require_admin()
    root = _trusted_root(shellcon.CSIDL_COMMON_APPDATA) / "SMTPProxy"
    if data_dir().absolute() != root:
        raise ServiceManagerError("ProgramData does not match the Windows known folder.")
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = win32security.SECURITY_DESCRIPTOR()
    attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorDacl(True, _acl(), False)
    attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorControl(
        win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED,
    )
    try:
        win32file.CreateDirectory(str(root), attributes)
    except pywintypes.error as error:
        if error.winerror != 183:
            raise
    _protect_tree(root)
    return root


def verify_data_dir() -> Path:
    root = _trusted_root(shellcon.CSIDL_COMMON_APPDATA) / "SMTPProxy"
    if data_dir().absolute() != root:
        raise ServiceManagerError("ProgramData does not match the Windows known folder.")
    _verify_tree(root)
    return root


def _verify_tree(root: Path, *, binary: bool = False) -> None:
    trusted = {"S-1-5-18", "S-1-5-32-544"}
    allowed = trusted | {"S-1-5-19"}
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or info.st_nlink > 1:
            raise ServiceManagerError("Unsafe link in protected storage.")
        descriptor = win32security.GetNamedSecurityInfo(
            str(path), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION,
        )
        owner = win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
        if owner not in (trusted if binary else allowed):
            raise PermissionError("Untrusted owner in protected storage.")
        acl = descriptor.GetSecurityDescriptorDacl()
        if acl is None:
            raise PermissionError("Protected storage has no DACL.")
        for index in range(acl.GetAceCount()):
            header, access, sid = acl.GetAce(index)
            if header[0] != win32security.ACCESS_ALLOWED_ACE_TYPE:
                raise PermissionError("Unexpected ACL entry in protected storage.")
            sid_text = win32security.ConvertSidToStringSid(sid)
            if binary:
                write_mask = (ntsecuritycon.FILE_GENERIC_WRITE | win32con.DELETE
                              | win32con.WRITE_DAC | win32con.WRITE_OWNER
                              | win32con.GENERIC_ALL | win32con.GENERIC_WRITE)
                write_mask &= ~win32con.READ_CONTROL & ~win32con.SYNCHRONIZE
                if sid_text not in trusted and access & write_mask:
                    raise PermissionError("Installed binaries are writable by an untrusted account.")
            elif sid_text not in allowed:
                raise PermissionError("Protected data is accessible to an untrusted account.")
        if path.is_dir():
            pending.extend(path.iterdir())


def _wait(service, target: int, *, stopped_ok: bool = False) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        state = win32service.QueryServiceStatus(service)
        if state[1] == target:
            return
        if target == win32service.SERVICE_RUNNING and state[1] == win32service.SERVICE_STOPPED:
            if stopped_ok:
                return
            raise ServiceManagerError(
                f"Service startup failed (Windows code {state[3]}, service {state[4]}).")
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for the Windows service.")
        time.sleep(0.2)


def install() -> None:
    _require_admin()
    executable = _installed_executable("app_service.exe")
    _protect_tree(executable.parent, binary=True)
    initialize_data_dir()
    if installed():
        with _service(win32service.SERVICE_QUERY_CONFIG) as service:
            config = win32service.QueryServiceConfig(service)
            if config[3] != f'"{executable}"' or config[7].lower() != "nt authority\\localservice":
                raise ServiceManagerError("Existing service has an unexpected binary path or account.")
        return
    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CREATE_SERVICE)
    service = None
    try:
        service = win32service.CreateService(
            manager, SERVICE_NAME, "SMTPProxy", win32service.SERVICE_ALL_ACCESS,
            win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_DEMAND_START,
            win32service.SERVICE_ERROR_NORMAL, f'"{executable}"', None, 0, None,
            "NT AUTHORITY\\LocalService", None,
        )
        win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_DESCRIPTION,
                                        "Local loopback SMTP relay")
        win32service.ChangeServiceConfig2(service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS, {
            "ResetPeriod": 86400, "RebootMsg": "", "Command": "",
            "Actions": [(win32service.SC_ACTION_RESTART, 30000),
                        (win32service.SC_ACTION_RESTART, 60000),
                        (win32service.SC_ACTION_NONE, 0)],
        })
        win32service.ChangeServiceConfig2(
            service, win32service.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True,
        )
    except Exception:
        if service is not None:
            win32service.DeleteService(service)
        raise
    finally:
        if service is not None:
            win32service.CloseServiceHandle(service)
        win32service.CloseServiceHandle(manager)


def start() -> None:
    _require_admin()
    with _service(win32service.SERVICE_START | win32service.SERVICE_QUERY_STATUS) as service:
        state = win32service.QueryServiceStatus(service)[1]
        if state == win32service.SERVICE_STOP_PENDING:
            _wait(service, win32service.SERVICE_STOPPED)
            state = win32service.SERVICE_STOPPED
        if state == win32service.SERVICE_STOPPED:
            win32service.StartService(service, None)
        _wait(service, win32service.SERVICE_RUNNING)


def stop() -> None:
    _require_admin()
    try:
        with _service(win32service.SERVICE_STOP | win32service.SERVICE_QUERY_STATUS) as service:
            state = win32service.QueryServiceStatus(service)[1]
            if state == win32service.SERVICE_STOPPED:
                return
            if state == win32service.SERVICE_START_PENDING:
                _wait(service, win32service.SERVICE_RUNNING, stopped_ok=True)
                state = win32service.QueryServiceStatus(service)[1]
                if state == win32service.SERVICE_STOPPED:
                    return
            if state != win32service.SERVICE_STOP_PENDING:
                win32service.ControlService(service, win32service.SERVICE_CONTROL_STOP)
            _wait(service, win32service.SERVICE_STOPPED)
    except pywintypes.error as error:
        if not _missing(error) and error.winerror != 1072:
            raise


def restart() -> None:
    _require_admin()
    stop()
    start()


def remove() -> None:
    _require_admin()
    stop()
    try:
        with _service(win32con.DELETE) as service:
            win32service.DeleteService(service)
    except pywintypes.error as error:
        if not _missing(error) and error.winerror != 1072:
            raise
    deadline = time.monotonic() + WAIT_SECONDS
    while installed():
        if time.monotonic() >= deadline:
            raise TimeoutError("Service deletion is pending; close service management windows.")
        time.sleep(0.2)


def set_autostart(enabled: bool) -> None:
    _require_admin()
    with _service(win32service.SERVICE_CHANGE_CONFIG) as service:
        win32service.ChangeServiceConfig(
            service, win32service.SERVICE_NO_CHANGE,
            win32service.SERVICE_AUTO_START if enabled else win32service.SERVICE_DEMAND_START,
            win32service.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None,
        )
        win32service.ChangeServiceConfig2(
            service, win32service.SERVICE_CONFIG_DELAYED_AUTO_START_INFO, bool(enabled),
        )


def elevate_gui() -> None:
    executable = _installed_executable("app_gui.exe")
    _verify_tree(executable.parent, binary=True)
    result = win32api.ShellExecute(0, "runas", str(executable), None, str(executable.parent), 1)
    if result <= 32:
        raise OSError("Windows could not open the administrator GUI.")
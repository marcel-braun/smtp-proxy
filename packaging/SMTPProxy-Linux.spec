from importlib.metadata import distribution
from pathlib import Path
import sys
import sysconfig

from PyInstaller.building.build_main import Analysis
from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(globals()["SPECPATH"]).parent
if sys.platform != "linux" or sys.maxsize <= 2**32:
    raise SystemExit("A 64-bit Linux build host is required.")

datas = copy_metadata("smtp-proxy", recursive=True)
datas += collect_data_files("PySide6", includes=["**/LICENSE*", "**/COPYING*", "**/NOTICE*"])
datas.append((str(root / ".env.example"), "."))
python_license_candidates = [
    Path(sys.base_prefix) / "LICENSE.txt",
    Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    Path("/usr/share/doc/python3.12/copyright"),
]
for license_directory in (Path("/usr/share/licenses/python3.12"),
                          Path("/usr/share/licenses/python3")):
    for pattern in ("LICENSE*", "COPYING*", "copyright"):
        python_license_candidates.extend(license_directory.glob(pattern))
python_license = next((path for path in python_license_candidates if path.is_file()), None)
if python_license is None:
    raise SystemExit("Python license text was not found; install the Python license documentation.")
datas.append((str(python_license), "licenses/Python"))
for source, destination in list(datas):
    if source.endswith(".dist-info"):
        package = Path(source).name.split("-")[0].replace("_", "-")
        for item in distribution(package).files or []:
            if any(part.lower().startswith(("license", "copying", "notice"))
                   for part in item.parts):
                location = distribution(package).locate_file(item)
                if location.is_file():
                    datas.append((str(location), f"licenses/{package}/{Path(item).parent}"))

common = dict(pathex=[str(root / "src")], binaries=[], hookspath=[], hooksconfig={},
              runtime_hooks=[], noarchive=False)
windows_only = ["smtp_proxy.service_manager", "win32com", "win32api", "win32con", "win32file",
                "win32security", "win32service", "win32serviceutil", "servicemanager",
                "pywintypes", "ntsecuritycon", "pythoncom"]
gui = Analysis([str(root / "app_gui.py")], datas=datas, excludes=windows_only, **common)
cli = Analysis([str(root / "app_cli.py")], datas=[],
               excludes=[*windows_only, "PySide6"], **common)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="app_gui",
              debug=False, strip=False, upx=False, console=False,
              contents_directory="_internal")
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="app_cli",
              debug=False, strip=False, upx=False, console=True,
              contents_directory="_internal")
COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas,
        strip=False, upx=False, name="SMTPProxy")
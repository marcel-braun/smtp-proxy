from importlib.metadata import distribution
from pathlib import Path
import sys

from PyInstaller.building.build_main import Analysis
from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct,
    VSVersionInfo,
)

root = Path(globals()["SPECPATH"]).parent
datas = copy_metadata("smtp-proxy", recursive=True)
datas += collect_data_files("PySide6", includes=["**/LICENSE*", "**/COPYING*", "**/NOTICE*"])
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.is_file():
    raise SystemExit("Python LICENSE.txt is required for redistribution.")
datas.append((str(python_license), "licenses/Python"))
for source, destination in list(datas):
    if source.endswith(".dist-info"):
        package = Path(source).name.split("-")[0].replace("_", "-")
        for item in distribution(package).files or []:
            if any(part.lower().startswith(("license", "copying", "notice")) for part in item.parts):
                location = distribution(package).locate_file(item)
                if location.is_file():
                    datas.append((str(location), f"licenses/{package}/{Path(item).parent}"))

version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=(1, 0, 0, 0), prodvers=(1, 0, 0, 0), mask=0x3F,
                      flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "Marcel Braun"),
        StringStruct("FileDescription", "SMTPProxy"),
        StringStruct("FileVersion", "1.0.0"),
        StringStruct("ProductName", "SMTPProxy"),
        StringStruct("ProductVersion", "1.0.0"),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
common = dict(pathex=[str(root / "src")], binaries=[], hookspath=[], hooksconfig={},
              runtime_hooks=[], noarchive=False)
gui = Analysis([str(root / "app_gui.py")], datas=datas, hiddenimports=["win32timezone"],
               **common)
service = Analysis([str(root / "app_service.py")], datas=[],
                   hiddenimports=["win32timezone", "servicemanager", "pythoncom", "pywintypes",
                                  "smtp_proxy.runtime", "smtp_proxy.windows_service"],
                   excludes=["PySide6"], **common)
cli = Analysis([str(root / "app_cli.py")], datas=[], hiddenimports=["win32timezone"],
               excludes=["PySide6"], **common)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="app_gui",
              debug=False, strip=False, upx=False, console=False, version=version,
              uac_admin=False, contents_directory="_internal")
service_exe = EXE(PYZ(service.pure), service.scripts, [], exclude_binaries=True,
                  name="app_service", debug=False, strip=False, upx=False, console=True,
                  version=version, uac_admin=False, contents_directory="_internal")
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
          name="app_cli", debug=False, strip=False, upx=False, console=True,
          version=version, uac_admin=False, contents_directory="_internal")
COLLECT(gui_exe, service_exe, cli_exe, gui.binaries, gui.datas,
    service.binaries, service.datas, cli.binaries, cli.datas,
        strip=False, upx=False, name="SMTPProxy")
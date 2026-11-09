[CmdletBinding()]
param(
    [string]$PythonExe = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    [string]$ISCC,
    [switch]$SkipInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$Executable, [string[]]$ArgumentList)
    & $Executable @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable $($ArgumentList -join ' ')"
    }
}

try {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
        throw 'A Windows x64 build host is required.'
    }
    Set-Location $PSScriptRoot
    if (-not (Test-Path $PythonExe -PathType Leaf)) {
        $PythonExe = (Get-Command $PythonExe -ErrorAction Stop).Source
    }
    Invoke-Checked $PythonExe @('-c', 'import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize("P") == 8, "Python 3.12 x64 is required"')
    foreach ($entry in @('app_gui.py', 'app_service.py', 'app_cli.py')) {
        if (-not (Test-Path $entry -PathType Leaf)) { throw "Missing entry point: $entry" }
    }
    if (-not $SkipInstaller) {
        if (-not $ISCC) {
            $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
            if ($compiler) { $ISCC = $compiler.Source }
            foreach ($candidate in @(
                    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
                )) {
                if (-not $ISCC -and (Test-Path $candidate -PathType Leaf)) { $ISCC = $candidate }
            }
        }
        if (-not $ISCC -or -not (Test-Path $ISCC -PathType Leaf)) {
            throw 'Inno Setup 6 ISCC.exe not found. Supply -ISCC or explicitly use -SkipInstaller.'
        }
    }
    $buildPython = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
    if (-not (Test-Path $buildPython -PathType Leaf)) {
        Invoke-Checked $PythonExe @('-m', 'venv', '.venv-build')
    }
    Invoke-Checked $buildPython @('-c', 'import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize("P") == 8 and sys.prefix != sys.base_prefix; assert "include-system-site-packages = false" in ( __import__("pathlib").Path(sys.prefix) / "pyvenv.cfg").read_text().lower()')
    Invoke-Checked $buildPython @('-m', 'pip', 'install', 'pip==25.2', 'setuptools==80.9.0', 'wheel==0.45.1')
    Invoke-Checked $buildPython @('-m', 'pip', 'install', '--no-build-isolation', '.[dev,build]')
    Invoke-Checked $buildPython @('-m', 'pytest', '-q')
    Invoke-Checked $buildPython @('-m', 'ruff', 'check', 'src', 'tests', 'app_gui.py', 'app_service.py', 'app_cli.py', 'packaging/SMTPProxy.spec')
    Invoke-Checked $buildPython @('-m', 'PyInstaller', '--noconfirm', '--clean', 'packaging/SMTPProxy.spec')
    foreach ($artifact in @('app_gui.exe', 'app_service.exe', 'app_cli.exe', '_internal\PySide6\plugins\platforms\qwindows.dll')) {
        if (-not (Test-Path (Join-Path 'dist\SMTPProxy' $artifact) -PathType Leaf)) {
            throw "Missing onedir artifact: $artifact"
        }
    }
    Invoke-Checked (Join-Path $PSScriptRoot 'dist\SMTPProxy\app_service.exe') @('--help')
    Invoke-Checked (Join-Path $PSScriptRoot 'dist\SMTPProxy\app_cli.exe') @('--help')
    Invoke-Checked (Join-Path $PSScriptRoot 'dist\SMTPProxy\app_gui.exe') @('--smoke-test')
    Get-ChildItem 'dist\SMTPProxy' -Filter '*.exe' | Get-FileHash -Algorithm SHA256 | Format-Table -AutoSize
    if ($SkipInstaller) {
        Write-Warning 'SkipInstaller selected: onedir bundle built; NO INSTALLER WAS PRODUCED by this run.'
    }
    else {
        Invoke-Checked $ISCC @('packaging\installer.iss')
        $setup = Join-Path $PSScriptRoot 'dist\SMTPProxy-Setup-1.0.0.exe'
        if (-not (Test-Path $setup -PathType Leaf)) { throw 'Compiler did not produce the expected installer.' }
        Get-FileHash $setup -Algorithm SHA256 | Format-List
        Write-Host "Installer: $setup"
    }
    exit 0
}
catch {
    Write-Error -ErrorAction Continue $_
    exit 1
}
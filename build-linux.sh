#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "A 64-bit x86_64 Linux build host is required." >&2
    exit 1
fi

PYTHON="${PYTHON:-python3.12}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.12 was not found. Set PYTHON to its executable." >&2
    exit 1
fi
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'

BUILD_ENV="$ROOT/.venv-build-linux"
BUILD_PYTHON="$BUILD_ENV/bin/python"
if [[ ! -x "$BUILD_PYTHON" ]]; then
    "$PYTHON" -m venv "$BUILD_ENV"
fi
"$BUILD_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12) and sys.prefix != sys.base_prefix'
"$BUILD_PYTHON" -m pip install --upgrade pip==25.2 setuptools==80.9.0 wheel==0.45.1
"$BUILD_PYTHON" -m pip install --no-build-isolation '.[dev,build]'
QT_QPA_PLATFORM=offscreen "$BUILD_PYTHON" -m pytest -q
"$BUILD_PYTHON" -m ruff check src tests app_gui.py app_service.py app_cli.py \
    packaging/SMTPProxy-Linux.spec
"$BUILD_PYTHON" -m PyInstaller --noconfirm --clean \
    --distpath dist/linux-x86_64 --workpath build/linux-x86_64 \
    packaging/SMTPProxy-Linux.spec

APP="$ROOT/dist/linux-x86_64/SMTPProxy"
if [[ ! -d "$APP/_internal" ]]; then
    echo "Missing Linux bundle directory: _internal" >&2
    exit 1
fi
for artifact in app_gui app_cli _internal/.env.example; do
    if [[ ! -f "$APP/$artifact" ]]; then
        echo "Missing Linux bundle artifact: $artifact" >&2
        exit 1
    fi
done
"$APP/app_cli" --help >/dev/null
QT_QPA_PLATFORM=offscreen "$APP/app_gui" --smoke-test
sha256sum "$APP/app_gui" "$APP/app_cli"
printf 'Linux bundle: %s\n' "$APP"
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="BluetoothGeminiChat"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 non trovato"
  exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r "$ROOT_DIR/desktop/requirements.txt" pyinstaller

cd "$ROOT_DIR"
rm -rf build "$ROOT_DIR/dist/$APP_NAME" "$ROOT_DIR/dist/$APP_NAME.app"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --paths "$ROOT_DIR/desktop" \
  --collect-submodules bleak \
  "$ROOT_DIR/desktop/app.py"

if [ -d "$ROOT_DIR/dist/$APP_NAME.app" ]; then
  echo "Build macOS pronta: $ROOT_DIR/dist/$APP_NAME.app"
elif [ -d "$ROOT_DIR/dist/$APP_NAME" ]; then
  echo "Build desktop pronta: $ROOT_DIR/dist/$APP_NAME"
else
  echo "Build completata, ma output non trovato in dist/"
  exit 1
fi

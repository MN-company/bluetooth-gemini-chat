#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="BluetoothGeminiChat"
BUNDLE_ID="com.mncompany.bluetoothgeminichat"
OS_NAME="$(uname -s)"
APP_VERSION="$(
python3 - <<'PY'
import pathlib
import re
text = pathlib.Path("desktop/app.py").read_text(encoding="utf-8")
match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
print(match.group(1) if match else "0.1.0")
PY
)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 non trovato"
  exit 1
fi

python3 -m pip install --upgrade pip
python3 -m pip install -r "$ROOT_DIR/desktop/requirements.txt" pyinstaller

cd "$ROOT_DIR"
rm -rf build "$ROOT_DIR/dist/$APP_NAME" "$ROOT_DIR/dist/$APP_NAME.app"

PYI_ARGS=(
  --noconfirm
  --clean
  --windowed
  --name "$APP_NAME"
  --paths "$ROOT_DIR/desktop"
  --collect-submodules bleak
  --collect-submodules pystray
  --add-data "$ROOT_DIR/desktop/install_macos_quick_action.py:."
  --add-data "$ROOT_DIR/desktop/macos_quick_ask.sh:."
  --add-data "$ROOT_DIR/desktop/macos_quick_ask.py:."
)
if [ "$OS_NAME" = "Darwin" ]; then
  PYI_ARGS+=(--osx-bundle-identifier "$BUNDLE_ID")
fi
PYI_ARGS+=("$ROOT_DIR/desktop/app.py")
python3 -m PyInstaller "${PYI_ARGS[@]}"

if [ "$OS_NAME" = "Darwin" ] && [ -d "$ROOT_DIR/dist/$APP_NAME.app" ]; then
  echo "Build macOS pronta: $ROOT_DIR/dist/$APP_NAME.app"
  INFO_PLIST="$ROOT_DIR/dist/$APP_NAME.app/Contents/Info.plist"
  if [ -f "$INFO_PLIST" ]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$INFO_PLIST" >/dev/null 2>&1 \
      || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $APP_VERSION" "$INFO_PLIST"
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_VERSION" "$INFO_PLIST" >/dev/null 2>&1 \
      || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$INFO_PLIST"
  fi
  mkdir -p "$ROOT_DIR/dist/dmg-root"
  rm -rf "$ROOT_DIR/dist/dmg-root/$APP_NAME.app" "$ROOT_DIR/dist/dmg-root/Applications"
  cp -R "$ROOT_DIR/dist/$APP_NAME.app" "$ROOT_DIR/dist/dmg-root/"
  ln -s /Applications "$ROOT_DIR/dist/dmg-root/Applications"
  hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$ROOT_DIR/dist/dmg-root" \
    -ov \
    -format UDZO \
    "$ROOT_DIR/dist/$APP_NAME-macos.dmg"
  echo "DMG pronta: $ROOT_DIR/dist/$APP_NAME-macos.dmg"
elif [ "$OS_NAME" = "Linux" ] && [ -d "$ROOT_DIR/dist/$APP_NAME" ]; then
  echo "Build Linux pronta: $ROOT_DIR/dist/$APP_NAME"
  tar -C "$ROOT_DIR/dist" -czf "$ROOT_DIR/dist/$APP_NAME-linux.tar.gz" "$APP_NAME"
  echo "Tarball pronta: $ROOT_DIR/dist/$APP_NAME-linux.tar.gz"
elif [ -d "$ROOT_DIR/dist/$APP_NAME" ]; then
  echo "Build desktop pronta: $ROOT_DIR/dist/$APP_NAME"
else
  echo "Build completata, ma output non trovato in dist/"
  exit 1
fi

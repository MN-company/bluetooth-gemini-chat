#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANDROID_DIR="$ROOT_DIR/android/GeminiBluetoothBridge"
OUT_APK="$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"

if ! command -v gradle >/dev/null 2>&1; then
  echo "gradle non trovato"
  exit 1
fi

cd "$ANDROID_DIR"
gradle assembleDebug

mkdir -p "$ROOT_DIR/dist"
cp -f "$OUT_APK" "$ROOT_DIR/dist/app-debug.apk"

echo "APK pronta: $ROOT_DIR/dist/app-debug.apk"

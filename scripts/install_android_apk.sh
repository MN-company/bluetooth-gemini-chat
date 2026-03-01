#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK_PATH="${1:-$ROOT_DIR/dist/app-debug.apk}"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb non trovato. Installa Android platform-tools."
  exit 1
fi

if [ ! -f "$APK_PATH" ]; then
  echo "APK non trovata: $APK_PATH"
  exit 1
fi

adb start-server >/dev/null

DEVICE_COUNT="$(adb devices | awk 'NR>1 && $2=="device" {count++} END {print count+0}')"
if [ "$DEVICE_COUNT" -eq 0 ]; then
  echo "Nessun dispositivo adb online"
  echo "Abilita Debug USB e autorizza il computer sul telefono"
  exit 1
fi

if [ "$DEVICE_COUNT" -gt 1 ]; then
  echo "Trovati piu dispositivi. Passa seriale con: adb -s <serial> install -r $APK_PATH"
  adb devices
  exit 1
fi

adb install -r "$APK_PATH"
echo "Installazione APK completata"

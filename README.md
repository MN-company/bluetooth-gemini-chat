# Bluetooth Gemini Overlay

BLE bridge between desktop and Android phone for `Shot+Ask`.

The desktop app is now a background utility, not a full chat UI:
- macOS: menu bar app
- Windows: tray/background app
- BLE transport unchanged
- main interaction path: screenshot or clipboard -> Gemini on the phone -> overlay text on desktop

## Architecture

- Desktop:
  - `desktop/app.py`: settings window, menu bar/tray, overlay, shortcut inbox
  - `desktop/ble_client.py`: BLE central client
- Android:
  - `android/GeminiBluetoothBridge/.../BleServerManager.kt`: BLE server
  - `android/GeminiBluetoothBridge/.../BleKeepAliveService.kt`: request routing + Gemini + streaming

## Install

### Desktop release builds

Download the latest release assets:
- macOS: `BluetoothGeminiChat-macos.dmg`
- Windows: `BluetoothGeminiChat-windows.zip`
- Linux: `BluetoothGeminiChat-linux.tar.gz`

### Local desktop run

```bash
cd desktop
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

### Android

```bash
./scripts/build_android_apk.sh
./scripts/install_android_apk.sh
```

## Current desktop flow

1. Launch desktop app.
2. Keep Android bridge app active.
3. Auto-connect or use `Scan` + `Connect Selected`.
4. Trigger:
   - screenshot ask
   - clipboard ask
5. Read the streamed answer directly in the desktop overlay.

## Shortcut wrappers

### macOS

The app installs these wrappers into `~/.gemini_ble/`:
- `ask_gemini_ble_shot.sh`
- `ask_gemini_ble_clipboard.sh`
- `hide_gemini_ble_overlay.sh`
- `toggle_gemini_ble.sh`

These are intended for Apple Shortcuts / keyboard shortcuts.

### Windows / Linux

Built-in global shortcuts:
- `Ctrl+Shift+G`: Shot+Ask
- `Ctrl+Shift+C`: Clipboard Ask
- `Ctrl+Shift+H`: Hide overlay

## Performance strategy

This version improves responsiveness without breaking cross-platform BLE compatibility:
- smaller screenshot payloads before BLE upload
- faster partial streaming from Android
- high-priority transmission for partial/result/error messages
- reconnect based on stable `bridge_id`

L2CAP CoC and server-side connection-priority forcing were not adopted because the current cross-platform stack (`Bleak` + macOS + Windows) would become less portable and less stable.

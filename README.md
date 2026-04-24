# Bluetooth Gemini Chat

Bluetooth Gemini Chat is a simple idea made practical:

your Android phone runs Gemini, your desktop stays lightweight, and the two talk over Bluetooth Low Energy.

The goal is to make "take a screenshot, ask the model, read the answer immediately" feel fast enough to use all day, without forcing the desktop client to manage the AI API directly.

## What This Project Does

- Android acts as the server and talks to the Gemini API.
- Desktop clients connect over BLE and send prompts, screenshots, clipboard text, PDFs, and images.
- The system supports multiple desktop clients connected to the same phone.
- Responses stream back to the correct client.
- The desktop app can stay out of the way with tray or menu bar behavior and a lightweight answer overlay.

In practice, this is built for a workflow like:

1. you see a quiz or a question on screen
2. you trigger `Shot+Ask`
3. the screenshot goes to the phone
4. Gemini answers
5. the answer appears as a small overlay on the desktop

## Why It Exists

This repo is optimized around a very specific use case:

- one Android device as the AI bridge
- one or more desktop clients
- fast screenshot-based interactions
- as little UI friction as possible

That is why the desktop app includes global shortcuts, overlay answers, tray integration, device discovery, reconnect logic, and model selection.

## Main Features

### Desktop

- Multi-chat interface
- Attach images and PDFs
- Markdown rendering and streaming responses
- Device list with connection state
- Model selection from the desktop app
- Overlay answer shown on top of other apps
- Configurable overlay style:
  - position
  - color
  - opacity
  - size
  - auto-hide time
- Global shortcuts for:
  - `Shot+Ask`
  - `Hide/Show risposta`
- Tray on Windows and menu bar mode on macOS
- Auto-update check against GitHub releases

### Android

- BLE bridge service
- Gemini API integration
- Support for multiple connected clients
- Device-side request queueing
- Reconnect-friendly flow
- In-app update check
- Settings and permission helpers

## Platforms

- macOS
- Windows
- Linux
- Android

The desktop client is cross-platform.
The server side is Android.

## Recommended Setup

### If You Just Want To Use The App

Download the ready-made builds from GitHub Releases when available:

- macOS: `BluetoothGeminiChat-macos.dmg`
- Windows: `BluetoothGeminiChat-windows.zip`
- Linux: `BluetoothGeminiChat-linux.tar.gz`
- Android: APK from the release assets

If a release is missing a desktop artifact, the GitHub Actions workflow can generate it.

### If You Want To Run It Locally

Desktop:

```bash
./scripts/setup_desktop.sh
./scripts/run_desktop.sh
```

Android APK:

```bash
./scripts/build_android_apk.sh
./scripts/install_android_apk.sh
```

## First-Time Setup

### Android

1. Install the APK.
2. Open `Gemini Bridge`.
3. Insert your Gemini API key.
4. Grant Bluetooth permissions.
5. Disable battery optimization if your device is aggressive about background apps.

### Desktop

1. Open the desktop app.
2. Press `Scan`.
3. Choose your Android device.
4. Connect.
5. Send a prompt or use `Shot+Ask`.

## macOS Notes

On macOS you should allow:

- Bluetooth access
- Screen Recording
- Accessibility, if you want the best shortcut experience

The app also supports menu bar behavior and can hide the Dock icon.

## Current Shortcut Focus

Right now the two most important shortcuts are configurable directly inside the app:

- `Shot+Ask`
- `Hide/Show risposta`

On macOS they are registered as native global shortcuts, so they can work even when another app is focused.

## Useful Scripts

- `scripts/setup_desktop.sh` installs desktop dependencies
- `scripts/run_desktop.sh` launches the desktop client
- `scripts/build_desktop_bundle.sh` builds a local desktop bundle
- `scripts/build_android_apk.sh` builds the Android APK
- `scripts/install_android_apk.sh` installs the APK with ADB

## Important Paths

- Desktop app: `desktop/app.py`
- Desktop BLE client: `desktop/ble_client.py`
- Android BLE service: `android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/BleKeepAliveService.kt`
- Android Gemini client: `android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/GeminiApiClient.kt`

## Known Practical Notes

- API usage and billing depend on the Gemini API key configured on Android.
- On some Android vendors, especially Xiaomi/MIUI/HyperOS, disabling battery optimization is essential.
- BLE mesh is not the target architecture here. This project is built around one Android server and one or more BLE clients.

## Release Workflow

GitHub Actions can build the desktop packages and upload them to releases.

That means the project can ship:

- `.dmg` for macOS
- `.zip` for Windows
- `.tar.gz` for Linux
- APK for Android

## Status

This project is very close to a complete daily-use workflow:

- Android server
- desktop clients
- multi-client support
- screenshot-based interaction
- overlay answers
- configurable shortcuts
- cross-platform desktop packaging

The remaining work is mainly polish, testing, and release hardening.

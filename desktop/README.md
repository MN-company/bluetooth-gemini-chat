# Gemini BLE Overlay

Desktop utility for `Shot+Ask` over BLE.

## What changed

The desktop client is no longer a chat application.

It now does only three things:
- stay in the macOS menu bar or Windows tray/background
- keep a BLE link with the Android phone
- capture screenshot/clipboard, send to the phone, and show the streamed answer as overlay text

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Desktop UX

- Main window: settings only
- No chat composer
- No chat history
- No PDF/context UI
- Interaction path: screenshot shortcut, clipboard shortcut, or tray/menu action

## Settings

- `System Instruction`
- `Model`
- `Overlay position`
- `Overlay text color`
- `Overlay opacity`
- `Overlay text size`
- `Hide after`
- `Auto-connect on start`
- `Auto-retry known phone`

## Shortcuts

### macOS

Apple Shortcuts / shell wrappers:
- `~/.gemini_ble/ask_gemini_ble_shot.sh`
- `~/.gemini_ble/ask_gemini_ble_clipboard.sh`
- `~/.gemini_ble/hide_gemini_ble_overlay.sh`
- `~/.gemini_ble/toggle_gemini_ble.sh`

The app auto-installs/refreshes the wrappers on startup.

### Windows / Linux

Global hotkeys:
- `Ctrl+Shift+G`: Shot+Ask
- `Ctrl+Shift+C`: Clipboard Ask
- `Ctrl+Shift+H`: Hide overlay

## Notes

- Screenshot upload is optimized for speed by compressing area captures aggressively before BLE transfer.
- Partial streaming is prioritized on Android so the overlay starts updating sooner.
- BLE reconnect still relies on stable `bridge_id`, not on the rotating BLE address alone.

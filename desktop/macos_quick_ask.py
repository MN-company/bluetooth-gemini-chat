#!/usr/bin/env python3
"""Helper script called by macos_quick_ask.sh to append quick events."""
from __future__ import annotations

import json
import os
import sys
import time

inbox = sys.argv[1]
mode = (sys.argv[2] if len(sys.argv) > 2 else "quick_send").strip().lower()
overlay_prompt = (sys.argv[3] if len(sys.argv) > 3 else "").strip()
text = os.environ.get("GEMINI_INPUT_TEXT", "").strip()
os.makedirs(os.path.dirname(inbox), exist_ok=True)

if mode == "quick_send":
    if not text:
        sys.exit(0)
    payload = {
        "type": "quick_send",
        "text": text,
        "ts": time.time(),
    }
elif mode == "quick_overlay":
    payload = {
        "type": "quick_overlay",
        "prompt": overlay_prompt,
        "ts": time.time(),
    }
elif mode == "quick_clipboard":
    payload = {
        "type": "quick_clipboard_overlay",
        "prompt": overlay_prompt,
        "ts": time.time(),
    }
else:
    payload = {
        "type": "toggle_visibility",
        "ts": time.time(),
    }

with open(inbox, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

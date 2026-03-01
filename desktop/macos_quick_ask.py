#!/usr/bin/env python3
"""Helper script called by macos_quick_ask.sh to append to quick_inbox.jsonl."""
from __future__ import annotations

import json
import os
import sys
import time

inbox = sys.argv[1]
text = os.environ.get("GEMINI_INPUT_TEXT", "").strip()
if not text:
    sys.exit(0)

payload = {
    "type": "quick_send",
    "text": text,
    "ts": time.time(),
}

with open(inbox, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

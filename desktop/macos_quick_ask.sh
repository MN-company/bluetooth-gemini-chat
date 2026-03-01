#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INBOX_FILE="$SCRIPT_DIR/quick_inbox.jsonl"

if [ "$#" -gt 0 ]; then
  INPUT_TEXT="$*"
elif [ ! -t 0 ]; then
  INPUT_TEXT="$(cat)"
else
  INPUT_TEXT="$(pbpaste)"
fi

INPUT_TEXT="${INPUT_TEXT//$'\r'/}"
if [ -z "${INPUT_TEXT// }" ]; then
  exit 0
fi

printf '%s' "$INPUT_TEXT" | python3 - "$INBOX_FILE" <<'PY'
import json
import sys
import time

inbox = sys.argv[1]
text = sys.stdin.read().strip()
if not text:
    raise SystemExit(0)

payload = {
    "type": "quick_send",
    "text": text,
    "ts": time.time(),
}

with open(inbox, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
PY

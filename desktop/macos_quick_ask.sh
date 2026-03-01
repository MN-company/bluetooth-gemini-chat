#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INBOX_FILE="$SCRIPT_DIR/quick_inbox.jsonl"
HELPER_PY="$SCRIPT_DIR/macos_quick_ask.py"

# Collect input text
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

# Pass text via env var to avoid pipe+heredoc stdin conflict
GEMINI_INPUT_TEXT="$INPUT_TEXT" python3 "$HELPER_PY" "$INBOX_FILE"

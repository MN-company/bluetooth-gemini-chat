#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$HOME/.gemini_ble"
mkdir -p "$RUNTIME_DIR"
INBOX_FILE="$RUNTIME_DIR/quick_inbox.jsonl"
HELPER_PY="$SCRIPT_DIR/macos_quick_ask.py"
MODE="quick_send"
OVERLAY_PROMPT=""

if [ "${1:-}" = "--shot-ask" ] || [ "${1:-}" = "--overlay" ]; then
  MODE="quick_overlay"
  shift
  if [ "$#" -gt 0 ]; then
    OVERLAY_PROMPT="$*"
  fi
elif [ "${1:-}" = "--toggle" ]; then
  MODE="toggle_visibility"
  shift
fi

INPUT_TEXT=""
if [ "$MODE" = "quick_send" ]; then
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
fi

# Pass values via env var + args to avoid pipe+heredoc stdin conflict.
GEMINI_INPUT_TEXT="$INPUT_TEXT" python3 "$HELPER_PY" "$INBOX_FILE" "$MODE" "$OVERLAY_PROMPT"

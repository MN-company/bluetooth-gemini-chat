#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/desktop/.venv"

if [ ! -d "$VENV_DIR" ]; then
  "$ROOT_DIR/scripts/setup_desktop.sh"
fi

source "$VENV_DIR/bin/activate"
cd "$ROOT_DIR/desktop"
python app.py

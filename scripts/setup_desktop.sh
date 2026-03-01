#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/desktop/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 non trovato"
  exit 1
fi

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT_DIR/desktop/requirements.txt"

echo "Setup completato"
echo "Avvio: $ROOT_DIR/scripts/run_desktop.sh"

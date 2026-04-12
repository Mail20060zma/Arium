#!/usr/bin/env bash
set -euo pipefail

PROFILE="${1:-auto}"
PYTHON_MODE="${2:-check-only}"
VENV_DIR="${3:-.venv}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/scripts/install_env.py"

if [[ ! -f "$INSTALLER" ]]; then
  echo "Installer script not found: $INSTALLER"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Python launcher not found. Install Python 3.11 first."
  exit 1
fi

"$PYTHON_BIN" "$INSTALLER" --profile "$PROFILE" --python-mode "$PYTHON_MODE" --venv "$VENV_DIR"

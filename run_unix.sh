#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "需要 Python 3.10 或更高版本。" >&2
  exit 2
}
if [ ! -x "$VENV_PYTHON" ]; then
  if ! "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv"; then
    echo "无法创建虚拟环境。Ubuntu 请安装 python3-venv，macOS 请确认 Python 来自 python.org 或 Homebrew。" >&2
    exit 2
  fi
fi
"$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt"
exec "$VENV_PYTHON" "$SCRIPT_DIR/portable_launch.py" "$@"

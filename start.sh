#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

pick_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi

  return 1
}

install_requirements() {
  local py_bin="$1"

  "${py_bin}" -m pip install --disable-pip-version-check -r requirements.txt && return 0
  "${py_bin}" -m pip install --user --disable-pip-version-check -r requirements.txt && return 0

  "${py_bin}" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "${py_bin}" -m pip install --disable-pip-version-check -r requirements.txt && return 0
  "${py_bin}" -m pip install --user --disable-pip-version-check -r requirements.txt && return 0

  if command -v pip3 >/dev/null 2>&1; then
    pip3 install --disable-pip-version-check -r requirements.txt && return 0
    pip3 install --user --disable-pip-version-check -r requirements.txt && return 0
  fi

  if command -v pip >/dev/null 2>&1; then
    pip install --disable-pip-version-check -r requirements.txt && return 0
    pip install --user --disable-pip-version-check -r requirements.txt && return 0
  fi

  if command -v sudo >/dev/null 2>&1; then
    sudo "${py_bin}" -m pip install --disable-pip-version-check -r requirements.txt && return 0
  fi

  return 1
}

if ! PYTHON_BIN="$(pick_python)"; then
  echo "Python was not found. Install Python 3.10+ and relaunch."
  read -r -p "Press Enter to close..."
  exit 1
fi

if ! "${PYTHON_BIN}" - <<'PY'
import requests
import socks
import rich
PY
then
  echo "Missing required Python packages. Trying to install from requirements.txt..."
  if ! install_requirements "${PYTHON_BIN}"; then
    echo
    echo "Failed to install required packages automatically."
    echo "Run manually: ${PYTHON_BIN} -m pip install -r requirements.txt"
    read -r -p "Press Enter to close..."
    exit 1
  fi
fi

if ! "${PYTHON_BIN}" - <<'PY'
from sni_finder.settings import load_settings
import sys

settings = load_settings()
sys.exit(0 if str(getattr(settings, "vless_source", "")).strip() else 1)
PY
then
  echo
  echo "First-time setup is required before scanning."
  echo "Opening guided setup wizard..."
  echo
  if ! "${PYTHON_BIN}" scanner.py onboarding; then
    echo
    echo "Setup was cancelled or failed."
    echo "Log file: logs/scanner.log"
    read -r -p "Press Enter to close..."
    exit 1
  fi

  if ! "${PYTHON_BIN}" - <<'PY'
from sni_finder.settings import load_settings
import sys

settings = load_settings()
sys.exit(0 if str(getattr(settings, "vless_source", "")).strip() else 1)
PY
  then
    echo
    echo "vless_source is still empty. Please set it and relaunch."
    echo "Log file: logs/scanner.log"
    read -r -p "Press Enter to close..."
    exit 1
  fi
fi

if ! "${PYTHON_BIN}" scanner.py; then
  EXIT_CODE=$?
else
  EXIT_CODE=0
fi

echo
if [[ ${EXIT_CODE} -ne 0 ]]; then
  echo "Scanner exited with an error. Code=${EXIT_CODE}"
else
  echo "Scanner closed."
fi
echo "Log file: logs/scanner.log"
read -r -p "Press Enter to close..."
exit ${EXIT_CODE}

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

  return 1
}

if ! PYTHON_SYSTEM_BIN="$(pick_python)"; then
  echo "Python was not found. Install Python 3.10+ and relaunch."
  read -r -p "Press Enter to close..."
  exit 1
fi

# Check if a virtual environment is already active
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  echo "Using already active virtual environment: ${VIRTUAL_ENV}"
  PYTHON_BIN="python"
else
  # Detect existing venv folders (checks both venv and .venv)
  VENV_DIR=""
  if [[ -f "venv/bin/activate" ]]; then
    VENV_DIR="venv"
  elif [[ -f ".venv/bin/activate" ]]; then
    VENV_DIR=".venv"
  fi

  if [[ -n "${VENV_DIR}" ]]; then
    echo "Found existing virtual environment: ${VENV_DIR}"
    # shellcheck source=/dev/null
    source "${VENV_DIR}/bin/activate"
    PYTHON_BIN="python"
  else
    # Clean up incomplete venv folder if present (missing activation script)
    if [[ -d "venv" ]]; then
      echo "Detected incomplete venv folder. Cleaning it up..."
      rm -rf "venv"
    fi

    echo "Creating Python virtual environment (venv)..."
    if "${PYTHON_SYSTEM_BIN}" -m venv venv; then
      # shellcheck source=/dev/null
      source "venv/bin/activate"
      PYTHON_BIN="python"
    else
      echo "Warning: Failed to create virtual environment via '${PYTHON_SYSTEM_BIN} -m venv venv'."
      echo "Please make sure 'python3-venv' package is installed (e.g., 'sudo apt install python3-venv')."
      echo "Falling back to using system Python environment..."
      PYTHON_BIN="${PYTHON_SYSTEM_BIN}"
    fi
  fi
fi

if ! "${PYTHON_BIN}" - <<'PY'
import requests
import socks
import rich
PY
then
  echo "Missing required Python packages in the virtual environment. Installing from requirements.txt..."
  if ! install_requirements "${PYTHON_BIN}"; then
    echo
    echo "Failed to install required packages automatically."
    echo "Run manually inside the virtual environment: source .venv/bin/activate && pip install -r requirements.txt"
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
  echo "Starting first-time setup wizard..."
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

if command -v clear >/dev/null 2>&1; then
  clear
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
